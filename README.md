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
				"source": ["rtsps://camera-adres"],
				"name": ["Stal Rechts Achterin"]
			},
			"yolo": {
				"model": "/Users/cowcatcher/Desktop/CowCatcher - Custom/models/cowcatcherV17.onnx",
				"confidence": 0.85,
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
				"disk": {
					"directory": "/Users/cowcatcher/Desktop/video"
				}
			}
		}
	]
}
```

Met `summary` worden herhaalde detecties van dezelfde sprong samengevoegd: kort na
elkaar op dezelfde plek, of tegelijk gezien door twee camera's. Alleen de eerste
detectie van een sprong komt als melding binnen; om 08:00 en 16:00 volgt een overzicht
van alle sprongen. Zet `"send_events": false` om alleen het overzicht te krijgen. Met
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

De training bouwt voort op V17 en overschrijft het originele model niet.
`--update-config` maakt `config.json.bak` en activeert na succesvolle training
`cowcatcher-feedback.pt`. Start daarna de detector opnieuw. De eerste start kan
enkele minuten duren omdat het getrainde model naar ONNX wordt geëxporteerd.

Een succesvolle training eindigt zonder traceback en meldt zowel het bijgewerkte
configbestand als het opgeslagen model.

## Meer informatie

De volledige technische configuratiereferentie staat in
[detector/README.md](detector/README.md).
