import fs from 'node:fs/promises';
import path from 'node:path';
import { CONFIG_PATH, DETECTIONS_DIR } from './shared-paths';

async function listFolders(directoryPath: string): Promise<string[]> {
	try {
		const entries = await fs.readdir(directoryPath, { withFileTypes: true });
		return entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name);
	} catch {
		return [];
	}
}

/**
 * The folders the detector saves detections in, by the name shown as their
 * type; each holds approved/rejected/unvalidated folders. Without a
 * directory the disk exporter uses detections/<label>/ next to the program;
 * with one (e.g. /Users/cowcatcher/Desktop/video) it uses that folder, so it
 * is read from config.json. A review folder (review: true) has another
 * layout and is left out.
 */
export async function detectionFolders(): Promise<Map<string, string>> {
	const folders = new Map<string, string>();
	for (const name of await listFolders(DETECTIONS_DIR)) {
		folders.set(name, path.join(DETECTIONS_DIR, name));
	}
	let config: { detectors?: { exporters?: { disk?: unknown } }[] } | null = null;
	try {
		config = JSON.parse(await fs.readFile(CONFIG_PATH, 'utf8'));
	} catch {
		config = null;
	}
	const known = new Set(folders.values());
	for (const detector of config?.detectors ?? []) {
		const disk = detector?.exporters?.disk;
		for (const exporter of Array.isArray(disk) ? disk : disk ? [disk] : []) {
			const directory = (exporter as { directory?: unknown; review?: unknown })?.directory;
			if (
				typeof directory !== 'string' ||
				!directory ||
				(exporter as { review?: unknown }).review
			) {
				continue;
			}
			// The detector resolves it like Path("detections") / directory.
			const folder = path.isAbsolute(directory) ? directory : path.join(DETECTIONS_DIR, directory);
			if (known.has(folder)) continue;
			known.add(folder);
			let name = path.basename(folder) || folder;
			while (folders.has(name)) name += '+';
			folders.set(name, folder);
		}
	}
	return folders;
}
