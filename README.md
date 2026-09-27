# CowCatcher AI Detector

Deze fork draait CowCatcher op een Mac mini, verstuurt detecties via Telegram en
verzamelt gecontroleerde voorbeelden om het YOLO-model verder te trainen.

## Mac mini-indeling

```text
/Users/cowcatcher/Desktop/
├── CowCatcher - Custom/
│   ├── aidetector-osx-v0.7.5.command
│   ├── config.json
│   └── models/
│       ├── cowcatcherV17.pt
│       ├── cowcatcherV17.onnx
│       └── cowcatcher-feedback.pt
├── data/
│   ├── good/
│   ├── bad/
│   └── .telegram-feedback/
└── video/
```

Download de actuele macOS-build via de
[releases](https://github.com/StefHarmens/ai-detector/releases). Gebruik het
bestand `aidetector-osx-v0.7.5.zip`, niet het source-codearchief.

## Configuratie

Zet `config.json` naast het `.command`-bestand. De relevante paden zijn:

```json
{
	"$schema": "https://raw.githubusercontent.com/StefHarmens/ai-detector/main/config/config.schema.json",
	"detectors": [
		{
			"detection": {
				"source": [
					"rtsps://<nvr-ip>:7441/<sleutel-camera-1>",
					"rtsps://<nvr-ip>:7441/<sleutel-camera-2>",
					"rtsps://<nvr-ip>:7441/<sleutel-camera-3>",
					"rtsps://<nvr-ip>:7441/<sleutel-camera-4>",
					"rtsps://<nvr-ip>:7441/<sleutel-camera-5>"
				],
				"name": [
					"Stal Links PTZ Voorin",
					"Stal Rechts Voorin",
					"Stal Links Achterin",
					"Stal Rechts Achterin",
					"Stal Achterin Centraal"
				]
			},
			"yolo": {
				"model": "/Users/cowcatcher/Desktop/CowCatcher - Custom/models/cowcatcherV17.onnx",
				"confidence": 0.85,
				"review_confidence": 0.7,
				"include_trailing_time": 10,
				"frames_min": 8,
				"imgsz": 960
			},
			"exporters": {
				"telegram": {
					"token": "<bot-token>",
					"chat": "<chat-id>",
					"feedback_directory": "/Users/cowcatcher/Desktop/data",
					"summary": {
						"times": ["08:00", "16:00"],
						"camera_groups": [
							["Stal Links PTZ Voorin", "Stal Links Achterin", "Stal Achterin Centraal"],
							["Stal Rechts Voorin", "Stal Rechts Achterin", "Stal Achterin Centraal"]
						]
					}
				},
				"disk": [
					{
						"directory": "/Users/cowcatcher/Desktop/video"
					},
					{
						"directory": "/Users/cowcatcher/Desktop/data/twijfel",
						"review": true
					}
				]
			}
		}
	]
}
```

### Camera's en namen

`source` en `name` zijn twee lijsten die op volgorde bij elkaar horen: de eerste naam
hoort bij de eerste camera, de tweede naam bij de tweede camera, enzovoort. In het
voorbeeld hierboven:

| Plek | `source`                  | `name`                   |
| :--- | :------------------------ | :----------------------- |
| 1    | `...<sleutel-camera-1>`   | `Stal Links PTZ Voorin`  |
| 2    | `...<sleutel-camera-2>`   | `Stal Rechts Voorin`     |
| 3    | `...<sleutel-camera-3>`   | `Stal Links Achterin`    |
| 4    | `...<sleutel-camera-4>`   | `Stal Rechts Achterin`   |
| 5    | `...<sleutel-camera-5>`   | `Stal Achterin Centraal` |

Let op:

- Zet de namen in **dezelfde volgorde** als de camera's, anders krijgt een sprong de
  naam van een andere camera.
- Het RTSPS-adres zet je in UniFi Protect per camera aan en kopieer je daar, bij de
  camera-instellingen onder *Advanced* (de precieze plek verschilt per versie). Het
  adres bevat een geheime sleutel; die komt nooit in Telegram.
- Geef je minder namen dan camera's, dan heten de overige camera's `Camera 4`,
  `Camera 5`, enzovoort (het nummer is de plek in de lijst).
- De namen in `camera_groups` moeten **precies** gelijk zijn aan die in `name`,
  inclusief hoofdletters en spaties.
- De naam mag hetzelfde zijn als de naam in UniFi, maar dat hoeft niet.

Met `summary` worden detecties samengevoegd die kort na elkaar (binnen 2 minuten) op
dezelfde plek in beeld zijn, of die tegelijk door twee camera's gezien worden. Koeien
worden nog niet herkend: het gaat alleen om tijd en plek. Alleen de eerste detectie komt
als melding binnen; om 08:00 en 16:00 volgt een overzicht, bijvoorbeeld:

```text
🐄 Overzicht sprongen
23-09 16:00 – 24-09 08:00

7 sprongen op 4 momenten

Tik op een moment om de melding te zien.

[ ▶️ 21:05 · Stal Links Achterin + Stal Achterin Centraal ]
[ ▶️ 03:12–03:16 · Stal Rechts Voorin + Stal Rechts Achterin · 4x ]
[ ▶️ 05:40 · Stal Links PTZ Voorin ]
[ ▶️ 05:51 · Stal Links PTZ Voorin ]
```

Elk moment is een knop. Tik je erop, dan antwoordt de bot op de melding van dat moment;
tik op het citaat in dat antwoord om naar de melding met de video te springen. Op een
telefoon kort Telegram een lange knop in het midden af met `…`. Een moment waarvan geen
melding verstuurd is (bijvoorbeeld met `"send_events": false`), staat als gewone regel in
de tekst.

Elke regel of knop is één moment; `4x` betekent dat er op dat moment 4 keer kort na elkaar op
dezelfde plek gesprongen is. Een tweede camera die dezelfde sprong ziet, telt niet mee. Zet `"send_events": false` om alleen het overzicht te krijgen. Met
`camera_groups` geef je aan welke camera's hetzelfde deel van de stal zien (op
`name`); alleen die worden samengevoegd, zodat een sprong links en een sprong rechts op
hetzelfde moment als twee sprongen tellen. Alle teksten in Telegram zijn Nederlands. Zie
[detector/README.md](detector/README.md) voor alle opties.

Iedere Telegram-melding bevat **Goed**- en **Fout**-knoppen. Goed bewaart de
schone afbeelding en metadata in `data/good`; Fout bewaart ze in `data/bad`.
Een gewijzigde keuze ruimt de eerdere classificatie automatisch op.

## Detector starten

```bash
cd "/Users/cowcatcher/Desktop/CowCatcher - Custom"
chmod +x aidetector-osx-v0.7.5.command
xattr -dr com.apple.quarantine aidetector-osx-v0.7.5.command
./aidetector-osx-v0.7.5.command
```

### Automatisch herstarten

De detector start zichzelf opnieuw:

- als je `config.json` opslaat; wijzigingen gelden dus direct, zonder handmatige
  herstart;
- 5 seconden na een crash, ook als de config ongeldig is. Zodra je de fout in
  `config.json` herstelt, start hij weer normaal.

Bij een herstart worden meldingen die op dat moment worden verstuurd eerst afgemaakt.
Een sprong die op dat moment nog bezig is, telt niet mee. Stoppen doe je nog steeds
met `Ctrl+C`.

## Twijfelgevallen controleren

Een sprong wordt alleen een melding in Telegram als YOLO minstens `confidence` (0.85)
zeker is, in minstens `frames_min` beelden. Alles wat daar net niet aan komt, is het
nuttigst om het model van te leren. Daarom komt het in de map
`data/twijfel/` terecht, zonder melding:

- **Twijfel:** YOLO was tussen `review_confidence` (0.70) en `confidence` (0.85) zeker.
- **Te kort:** YOLO was wel zeker genoeg, maar in te weinig beelden (`frames_min`).

Elke gebeurtenis krijgt een eigen map met de video (`video.mp4`), het beeld met kaders
(`best.jpg`), het beeld zonder kaders (`clean.jpg`) en `metadata.json`. De mapnaam is
de tijd plus de camera, bijvoorbeeld `2026-09-27T03-12-00 Stal Rechts Voorin`.

Bekijk ze af en toe met het reviewprogramma (vanaf v0.8.0). De detector mag daarbij gewoon blijven
draaien:

```bash
cd "/Users/cowcatcher/Desktop/CowCatcher - Custom"

./aidetector-osx-v0.8.0.command review-feedback \
	--source "/Users/cowcatcher/Desktop/data/twijfel" \
	--data-root "/Users/cowcatcher/Desktop/data"
```

In de browser zie je per gebeurtenis de video en het beeld. Kies **Good** (`G`) als het
een sprong is, **Bad** (`B`) als het geen sprong is en **Skip** (`S`) als je het niet
weet. Good en Bad komen direct in `data/good` en `data/bad`, klaar voor de volgende
training. Stop je halverwege, dan ga je de volgende keer verder waar je was; **Undo**
maakt de laatste keuze ongedaan.

De meldingen in Telegram veranderen hierdoor niet: in een melding tellen en staan alleen
kaders vanaf 0.85. Wordt de map te vol, zet `review_confidence` dan hoger, bijvoorbeeld
op `0.75`. Laat je `review_confidence` weg, dan komen alleen de te korte sprongen in de
map.

## Model trainen

Stop eerst de actieve detector. Train daarna op Apple Silicon met de
gecontroleerde afbeeldingen uit `data/good` en `data/bad`:

```bash
cd "/Users/cowcatcher/Desktop/CowCatcher - Custom"

./aidetector-osx-v0.7.5.command train-feedback \
	--config config.json \
	--data-root "/Users/cowcatcher/Desktop/data" \
	--model "/Users/cowcatcher/Desktop/CowCatcher - Custom/models/cowcatcherV17.pt" \
	--output "/Users/cowcatcher/Desktop/CowCatcher - Custom/models/cowcatcher-feedback.pt" \
	--epochs 25 \
	--batch 4 \
	--device mps \
	--update-config
```

De training gebruikt standaard 2 hulpprocessen (`--workers 2`) om de beelden in te
laden. Ultralytics start er zelf 8, en op een Mac laadt elk proces een eigen kopie van
PyTorch, waardoor 16 GB werkgeheugen vol raakt. Is het geheugen nog steeds vol, probeer
dan `--workers 1` of `--batch 2`; de training duurt dan wat langer.

De training bouwt voort op V17 en overschrijft het originele model niet.
`--update-config` maakt `config.json.bak` en activeert na succesvolle training
`cowcatcher-feedback.pt`. Start daarna de detector opnieuw. De eerste start kan
enkele minuten duren omdat het getrainde model naar ONNX wordt geëxporteerd.

Een succesvolle training eindigt zonder traceback en meldt zowel het bijgewerkte
configbestand als het opgeslagen model.

## Meer informatie

De volledige technische configuratiereferentie staat in
[detector/README.md](detector/README.md).
