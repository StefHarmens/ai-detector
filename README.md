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
				],
				"hires": {
					"source": [
						"rtsps://<nvr-ip>:7441/<4k-sleutel-camera-1>",
						"rtsps://<nvr-ip>:7441/<4k-sleutel-camera-2>",
						"rtsps://<nvr-ip>:7441/<4k-sleutel-camera-3>",
						"rtsps://<nvr-ip>:7441/<4k-sleutel-camera-4>",
						"rtsps://<nvr-ip>:7441/<4k-sleutel-camera-5>"
					]
				}
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
					},
					"cows": {
						"herd_file": "/Users/cowcatcher/Desktop/koeienlijst.xlsx",
						"herd_categories": ["Koeien", "Vrouwelijk jongvee"]
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
dezelfde plek in beeld zijn, of die tegelijk door twee camera's gezien worden. Het
samenvoegen gaat alleen op tijd en plek; met `cows` (zie [Koeien herkennen](#koeien-herkennen))
komt er een telling per koe bij. Alleen de eerste detectie komt
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

## Koeien herkennen

Met `cows` stuurt de bot na elke melding één foto met beide koeien naast elkaar (A en B),
met knoppen:

```text
🐄 Wie zijn het?
A werd besprongen (gok): ✅ 30 (Bertha) · herkend 93%
B sprong (gok): ❓

Tik een nummer aan, of antwoord op deze foto met de nummers, eerst A dan B: 30 12
[ ✅ A: 30 (Bertha) · 93% ] [ A: 44 · 78% ]
[ B: 44 · 88% ] [ B: 12 · 80% ]
[ ✏️ Nummers typen ]
[ ❔ A onbekend ] [ ❔ B onbekend ]
[ 🔄 Andersom ] [ 🚫 Foto A ] [ 🚫 Foto B ]
```

- **Antwoorden** gaat het snelst door op de foto te antwoorden met twee nummers, eerst A
  dan B: `30 12`. Onbekend is een vraagteken (`? 12`). Een koe die de bot nog niet kent,
  typ je met haar levensnummer: `44 NL123456789 12`. Eén nummer vult de koe die nog open
  staat. Iets fout? Antwoord nog eens met de goede nummers.
- **Tikken** kan ook: een nummer aan, **✏️ Nummers typen**, of **❔ onbekend** per koe.
- Elke keuze zet de foto van die koe in haar map, met de stal weggemaskeerd. Daarvan leert
  de herkenning, dus in het begin moet je vaak antwoorden. Zodra een koe 5 foto's heeft en
  duidelijk herkend wordt, vult de bot haar zelf in (`✅ 30 · herkend 93%`); antwoord dan
  alleen nog als het fout is.
- **🔄 Andersom** draait om wie sprong en wie besprongen werd. Met *(gok)* weet de bot
  het niet zeker.
- **🚫 Foto A/B**: die uitsnede toont niet één koe van de sprong. Die foto gaat dan in geen
  enkele map.
- Vindt de bot geen twee losse koeien, vóór of na de sprong, dan toont hij de sprong zelf:
  links de koe die sprong, rechts ruimer de koe eronder. Je antwoordt op dezelfde manier
  (eerst wie sprong); deze foto's gaan niet in een koemap.
- Een sprong die je met **Fout** afkeurt, telt niet mee.

Het overzicht van 08:00 en 16:00 krijgt dan een telling per koe:

```text
Per koe (🔥 = besprongen, mogelijk tochtig):
🔥 30 (Bertha): 3× besprongen
🔥 12: 1× besprongen, 1× gesprongen
• 7: 2× gesprongen
• Niet herkend: 1 koe
```

### Demo

Zo ziet het er in Telegram uit. De demo draait de echte code op de stalbeelden in
`example/good`; alleen Telegram is nagebootst. De koeien, levensnummers en antwoorden van
de boer zijn verzonnen.

**Koeien en pinken invoeren**: de CSV-export naar de bot sturen. Pinken zonder halsband
komen erin op werknummer.

<img src="docs/demo/1-koeien-invoeren.png" alt="De boer stuurt koeien.csv; de bot leest 5 dieren in, 3 op halsbandnummer en 2 op werknummer" width="444">

**Een sprong beantwoorden**: na de melding komt één foto met beide koeien. Vindt de bot
geen twee losse koeien, dan toont hij de sprong per rol. De boer antwoordt op de foto met
twee nummers, eerst wie sprong.

<img src="docs/demo/2-sprong-beantwoorden.png" alt="Foto met links de koe die sprong en rechts de koe eronder; de boer antwoordt 30 12" width="444">

**Een pink krijgt een halsband**: nummer 12 gaat naar pink Nel, en pink Anna krijgt na het
afkalven halsband 31. Beide blijven hetzelfde dier.

<img src="docs/demo/3-pink-krijgt-halsband.png" alt="/wissel 12 geeft nummer 12 aan pink Nel; /koe 31 geeft pink Anna halsband 31" width="444">

**Zodra de bot koeien herkent** (voorbeeld, zonder echte foto): per koe de meest
gelijkende koeien als knop, en een koe die hij duidelijk herkent vult hij zelf in.

<img src="docs/demo/4-herkende-koeien.png" alt="Voorbeeld met knoppen per koe en een automatisch herkende koe 30" width="444">

**Het overzicht** van 08:00 en 16:00 telt per koe hoe vaak ze besprongen werd (🔥) en
hoe vaak ze zelf sprong.

<img src="docs/demo/5-overzicht.png" alt="Overzicht sprongen met de telling per koe" width="444">

Het hele gesprek staat in [docs/demo/koeherkenning.html](docs/demo/koeherkenning.html):
download het bestand en open het in een browser, dan kun je het stap voor stap afspelen.
GitHub zelf toont alleen de broncode. Na een wijziging maak je de demo opnieuw met:

```bash
cd detector
uv run python ../docs/demo/maak_demo.py
```

De schermafbeeldingen hierboven maak je daarna opnieuw met `koeherkenning.html#chat`, dat
alleen het gesprek toont.

### Eerst zelf testen

Wil je de koeherkenning eerst zelf proberen terwijl de boer alles houdt zoals het was, maak
dan een tweede Telegram-bot en zet een tweede chat in de lijst onder `telegram`. De boer
houdt zijn eigen bot en chat, zonder `cows`:

```json
"telegram": [
	{
		"token": "<bot-token-boer>",
		"chat": "<chat-id-boer>",
		"feedback_directory": "/Users/cowcatcher/Desktop/data",
		"summary": { "times": ["08:00", "16:00"] }
	},
	{
		"token": "<bot-token-test>",
		"chat": "<jouw-chat-id>",
		"feedback_directory": "/Users/cowcatcher/Desktop/data-test",
		"summary": { "times": ["08:00", "12:00", "16:00", "20:00"] },
		"cows": {
			"herd_file": "/Users/cowcatcher/Desktop/koeienlijst.xlsx",
			"herd_categories": ["Koeien", "Vrouwelijk jongvee"]
		}
	}
]
```

Gebruik echt een **aparte bot**: het commandomenu geldt voor alle chats van een bot, en
twee chats met dezelfde bot maar een andere `feedback_directory` halen elkaars knoppen
weg. De testchat krijgt een eigen `feedback_directory`, zodat jouw Goed/Fout-tikken niet
bij de trainingsdata van de boer komen. Is het goed, zet `cows` dan bij de boer en kopieer
`data-test/koeien` naar `data/koeien`, dan neemt hij de foto's die jij al hebt aangetikt
mee.

`detection.hires` geldt voor beide chats: de boer merkt daar niets van in Telegram, maar de
Mac mini gebruikt dan wel ongeveer 350 MB per 4K-stream, en de schijf-export bewaart een
extra `hires.jpg` per sprong.

### Levensnummer, halsbandnummer en werknummer

Koeien worden bewaard op hun **I&R-levensnummer**. Het nummer waarmee je een koe noemt, is
alleen een label met een tijdstip, want nummer 30 gaat naar een pink als de oude 30 weg is.
Zo blijven oude sprongen bij de oude koe. Dat nummer is het halsbandnummer, of bij een
pink zonder halsband haar werknummer. Antwoorden kan ook met een naam (`Anna 12`).
Commando's in de chat:

| Commando | Wat het doet |
| :------- | :----------- |
| `/koe 30 NL123456789 Bertha` | Nummer 30 hoort bij deze koe (naam mag weg). |
| `/wissel 30 NL987654321` | Nummer 30 gaat naar een andere koe. De bot vraagt of de oude koe weg is (dan gaat ze naar het archief en telt ze niet meer mee bij het herkennen) of dat alleen de halsbanden gewisseld zijn. |
| `/weg 30` | De koe met nummer 30 is van het bedrijf; haar sprongen blijven bewaard. |
| `/koeien` | Alle koeien met nummer, levensnummer en aantal foto's. |
| `/overzicht 7` | Sprongen per koe over de laatste 7 dagen. |

De commando's staan ook in het menu van de chat, onder de /-knop.

### Pinken

De camera bij de pinken werkt hetzelfde. Pinken hebben nog geen halsband, dus je voert ze
in met hun werknummer en naam: `/koe 5101 NL100000001 Anna`, of met de CSV-export. In de
CSV neemt de bot per regel het halsbandnummer, en als dat leeg is het werknummer. Koeien
en pinken kunnen dus in één bestand.

Kalft een pink af en krijgt ze een halsband, geef haar dan dat nummer: `/koe 31
NL100000001`. Ze blijft hetzelfde dier, met haar sprongen en foto's; alleen haar nummer
verandert vanaf dat moment.

Zet in de detector van de pinkencamera ook `"cows": {}` onder `telegram`, met dezelfde
`feedback_directory` als bij de koeien. Dan delen beide camera's één register met alle
dieren. Meldt de pinkencamera in een eigen chat, dan telt het overzicht in die chat alleen
de sprongen van de pinken.

### Koeienlijst uit het managementprogramma

Het makkelijkst: zet het pad naar de export uit het managementprogramma (Excel of CSV) in
`config.json` onder `cows`:

```json
"cows": {
	"herd_file": "/Users/cowcatcher/Desktop/koeienlijst.xlsx",
	"herd_categories": ["Koeien", "Vrouwelijk jongvee"]
}
```

De detector leest de lijst bij het starten, en opnieuw zodra het bestand verandert.
Een nieuwe export over het oude bestand heen opslaan is genoeg. De lijst is leidend:

- nieuwe dieren komen erbij;
- een ander nummer wordt overgenomen, ook als twee koeien van halsband ruilen;
- namen worden bijgewerkt;
- een dier dat niet meer in de lijst staat, gaat naar het archief, en komt met haar foto's
  terug als ze weer in de lijst staat.

Ontbreekt er in één keer meer dan een vijfde van de dieren, dan lijkt het een halve export
en archiveert de bot niemand; hij waarschuwt dan in de chat. Na elke wijziging stuurt hij
een kort bericht, bijvoorbeeld `📋 Koeienlijst bijgewerkt: 2 nieuw, 1 ander nummer, 1 weg
(archief).`

De bot zoekt zelf de kopregel, ook als er een titel boven staat. De export uit het
Lely-programma (kolommen `Diernr`, `Resp 1`, `Levensnummer`, `Gesl`, `Naam`, `Werknummer`,
`Diercat`) wordt zo gelezen:

- een koe met halsband (er staat een responder in `Resp 1`) krijgt haar **Diernr**;
- een pink zonder halsband (`Resp 1` leeg) krijgt haar **Werknummer**. Kalft ze af en
  krijgt ze een halsband, dan neemt de bot bij de volgende export haar Diernr over; haar
  sprongen en foto's blijven bij haar;
- alleen de categorieën uit `herd_categories` doen mee (standaard `Koeien` en
  `Vrouwelijk jongvee`, zoals in `Diercat`); `Vaarskalf`, `Mannelijk` en de rest worden
  overgeslagen, en mannelijke dieren (`Gesl` Mannelijk) altijd. Het bericht zegt hoeveel;
- `Levnr moeder` wordt nooit als levensnummer gelezen.

Andere exports met kolommen zoals `Halsbandnummer` en `Werknummer` werken ook: per dier
het halsbandnummer, en anders het werknummer.

Zonder `herd_file` kan het ook eenmalig: stuur de export (Excel of CSV) als bestand naar de
bot. Op de Mac mini kan het ook met:

```bash
./aidetector-osx-v0.8.0.command import-koeien ~/Desktop/koeienlijst.xlsx
```

Het levensnummer wordt op vorm gecontroleerd (landcode en 9 tot 12 cijfers), niet op het
controlecijfer.

### 4K-beelden

Zonder 4K werkt het ook, maar dan op de uitsnede uit het 1280-beeld, waarin het vachtpatroon
minder scherp is. Zet in UniFi Protect per camera de RTSPS-stream **High** aan en zet die
adressen in `detection.hires`, in dezelfde volgorde als `source`:

```json
"detection": {
	"source": ["rtsps://<nvr-ip>:7441/<sleutel-camera-1>", "..."],
	"name": ["Stal Links PTZ Voorin", "..."],
	"hires": {
		"source": ["rtsps://<nvr-ip>:7441/<4k-sleutel-camera-1>", "..."]
	}
},
"exporters": {
	"telegram": {
		"...": "...",
		"summary": { "times": ["08:00", "16:00"] },
		"cows": {}
	}
}
```

De detector bewaart van elke 4K-camera 1 beeld per seconde van de laatste 90 seconden.
Elke 4K-stream kost ongeveer 350 MB geheugen. Telegram weigert foto's boven 10 MB en toont
ze hooguit 2560 pixels breed, dus foto's naar Telegram worden verkleind tot 2560 pixels en
onder 9,5 MB gehouden. De koemappen en `hires.jpg` houden de volle 4K-kwaliteit.

Per sprong bewaart de bot in `data/koeien/.meldingen/<id>/` de foto's, een `controle.jpg`
(het 4K-beeld met het kader van de sprong: valt dat niet op de koeien, dan lopen de twee
streams uit elkaar) en in `beelden/` een paar beelden van vóór en na de sprong. Die
beelden worden na 14 dagen opgeruimd. Een camera zonder 4K zet je op `null`.
De mappen staan in `data/koeien/`; zie [detector/README.md](detector/README.md) voor
alle opties.

## Detector starten

Het programma leest `config.json` uit de map waar het zelf staat. Zet een nieuwe versie
dus eerst in dezelfde map als `config.json` (niet starten vanuit Downloads): anders maakt
hij daar een lege `config.json` en blijft hij melden `detectors: Field required`.

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
- 5 seconden na een crash;
- bij een fout in `config.json` (bijvoorbeeld een ontbrekende komma) toont hij de foute
  regel en welke regel waarschijnlijk een komma mist, en wacht hij tot je het bestand
  opslaat. Camerasleutels en tokens staan daarbij onleesbaar in het log.

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
