import logging
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

import cv2
import numpy as np
import requests
from numpy import ndarray

from aidetector.cows.registry import CowRegistry

logger = logging.getLogger(__name__)

_SIZE = 224
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
EMBEDDINGS_FOLDER = ".embeddings"

Embed = Callable[[ndarray], ndarray]


# Two chats with the same data folder each have a cow service; they must not
# download the model into the same file at once.
_DOWNLOAD_LOCK = Lock()


def model_file(model: str, directory: Path) -> Path:
    """Downloads the model once when it is a URL."""
    if "://" not in model:
        return Path(model)
    path = directory / ".model" / Path(model.split("?")[0]).name
    with _DOWNLOAD_LOCK:
        if path.is_file():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading recognition model %s", model)
        # Its own file, so a download that broke off earlier or runs in
        # another program does not get in the way.
        with NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as file:
            temporary = Path(file.name)
        try:
            with requests.get(model, stream=True, timeout=60) as response:
                response.raise_for_status()
                with temporary.open("wb") as file:
                    for chunk in response.iter_content(1 << 20):
                        file.write(chunk)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def square(image: ndarray) -> ndarray:
    """Pads the crop to a square, so a long cow is not squeezed."""
    height, width = image.shape[:2]
    size = max(height, width)
    padded = np.full((size, size, 3), 114, dtype=np.uint8)
    top, left = (size - height) // 2, (size - width) // 2
    padded[top : top + height, left : left + width] = image
    return padded


def dinov2_embedder(model_path: Path) -> Embed:
    import onnxruntime as ort

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    def embed(image: ndarray) -> ndarray:
        rgb = cv2.cvtColor(square(image), cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (_SIZE, _SIZE), interpolation=cv2.INTER_AREA)
        pixels = ((resized.astype(np.float32) / 255 - _MEAN) / _STD).transpose(2, 0, 1)
        tokens = np.asarray(session.run(None, {input_name: pixels[None]})[0])[0]
        # The class token describes the whole cow, the mean of the patches her
        # coat pattern.
        vector = np.concatenate([tokens[0], tokens[1:].mean(axis=0)])
        return vector / max(float(np.linalg.norm(vector)), 1e-6)

    return embed


class Gallery:
    """Compares a cow with the photos in the folders of the cows that are on
    the farm now. Embeddings are cached next to the photos."""

    def __init__(self, registry: CowRegistry, embed: Embed):
        self.registry = registry
        self.embed = embed
        self.cache: dict[Path, tuple[float, ndarray]] = {}
        self.lock = Lock()

    def match(self, image: ndarray) -> tuple[ndarray, list[tuple[str, float]]]:
        """Returns the embedding of the cow and the cows that look most like
        her, best first, with the similarity of their closest photo."""
        embedding = self.embed(image)
        scores = []
        for cow in self.registry.active_cows():
            vectors = self._embeddings(cow.life_number)
            if len(vectors):
                scores.append((cow.life_number, float(np.max(vectors @ embedding))))
        scores.sort(key=lambda item: item[1], reverse=True)
        return embedding, scores

    def _embeddings(self, life_number: str) -> ndarray:
        vectors = []
        for photo in self.registry.photos(life_number):
            try:
                vectors.append(self._embedding(photo))
            except Exception:
                logger.warning("Skipping cow photo %s", photo, exc_info=True)
        return np.array(vectors)

    def _embedding(self, photo: Path) -> ndarray:
        modified = photo.stat().st_mtime
        with self.lock:
            cached = self.cache.get(photo)
        if cached and cached[0] == modified:
            return cached[1]
        stored = photo.parent / EMBEDDINGS_FOLDER / f"{photo.stem}.npy"
        if stored.is_file() and stored.stat().st_mtime >= modified:
            vector = np.load(stored)
        else:
            image = cv2.imread(str(photo))
            if image is None:
                raise ValueError(f"Cannot read {photo}")
            vector = self.embed(image)
            stored.parent.mkdir(parents=True, exist_ok=True)
            np.save(stored, vector)
        with self.lock:
            self.cache[photo] = (modified, vector)
        return vector
