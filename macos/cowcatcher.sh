#!/bin/bash
# CowCatcher for macOS: installs the detector and the web interface once as
# background services, and keeps them up to date from the GitHub releases.
#
#   bash cowcatcher.sh install      once, from Terminal
#   cowcatcher.sh status            versions and whether they run
#   cowcatcher.sh update            check for a new version now
#   cowcatcher.sh logs [detector|web|updater]
#   cowcatcher.sh rollback detector|web
#   cowcatcher.sh stop|start detector|web
#   cowcatcher.sh uninstall         stops the services, keeps all files
#
# launchd keeps both services running (also after a restart of the Mac) and
# runs "update" every 15 minutes. A new version that does not stay up is
# replaced by the previous one and skipped from then on. A stopped service
# starts again with the next update or restart of the Mac.

set -uo pipefail

REPO="StefHarmens/ai-detector"
APP_DIR="${COWCATCHER_DIR:-$HOME/CowCatcher}"
STATE_DIR="$APP_DIR/updater"
SETTINGS_FILE="$STATE_DIR/settings"
LOG_DIR="$HOME/Library/Logs/CowCatcher"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL_PREFIX="nl.cowcatcher"
COMPONENTS="detector web"
CHECK_INTERVAL=900
# Unpacking the detector and loading the models takes a while; a version that
# still runs after this is considered good.
HEALTH_SECONDS=120
LOG_MAX_BYTES=$((50 * 1024 * 1024))
LOG_KEEP_BYTES=$((10 * 1024 * 1024))

# Defaults, overridden by the settings file.
CHANNEL=beta
WEB_PORT=80

load_settings() {
	if [ -f "$SETTINGS_FILE" ]; then
		# shellcheck source=/dev/null
		. "$SETTINGS_FILE"
	fi
}

log() {
	printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
	echo "Fout: $*" >&2
	exit 1
}

confirm() {
	if [ "${ASSUME_YES:-0}" = 1 ]; then
		return 0
	fi
	local answer
	read -r -p "$1 [j/N] " answer </dev/tty || return 1
	case "$answer" in
	j | J | ja | Ja | y | Y | yes) return 0 ;;
	*) return 1 ;;
	esac
}

label_of() {
	echo "$LABEL_PREFIX.$1"
}

plist_of() {
	echo "$AGENTS_DIR/$(label_of "$1").plist"
}

binary_of() {
	case "$1" in
	detector) echo "$APP_DIR/aidetector" ;;
	web) echo "$APP_DIR/aidetector-web" ;;
	esac
}

log_of() {
	echo "$LOG_DIR/$1.log"
}

version_file_of() {
	echo "$STATE_DIR/$1.version"
}

skip_file_of() {
	echo "$STATE_DIR/$1.skip"
}

installed_version() {
	cat "$(version_file_of "$1")" 2>/dev/null || true
}

file_size() {
	stat -f %z "$1" 2>/dev/null || echo 0
}

# --- GitHub releases ---------------------------------------------------------

# Prints "version zip-url sha256 script-url" of the newest release of a
# component that is newer than the installed one, or nothing. Tags look like
# "detector/v0.8.0-beta.23"; a version with "-" is a beta and only taken on
# the beta channel. "-" stands for a missing sha256 or script.
PICK_RELEASE_JS=$(
	cat <<'EOF'
ObjC.import('Foundation');

function parse(version) {
	const match = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/.exec(version);
	if (!match) return null;
	return { core: [+match[1], +match[2], +match[3]], pre: match[4] ? match[4].split('.') : [] };
}

// Semantic versioning order: 0.8.0-beta.2 < 0.8.0-beta.10 < 0.8.0.
function compare(a, b) {
	for (let i = 0; i < 3; i++) if (a.core[i] !== b.core[i]) return a.core[i] - b.core[i];
	if (!a.pre.length || !b.pre.length) return b.pre.length - a.pre.length;
	for (let i = 0; i < Math.max(a.pre.length, b.pre.length); i++) {
		const x = a.pre[i], y = b.pre[i];
		if (x === undefined) return -1;
		if (y === undefined) return 1;
		const xNumber = /^\d+$/.test(x), yNumber = /^\d+$/.test(y);
		if (xNumber && yNumber) {
			if (+x !== +y) return +x - +y;
		} else if (xNumber !== yNumber) {
			return xNumber ? -1 : 1;
		} else if (x !== y) {
			return x < y ? -1 : 1;
		}
	}
	return 0;
}

function run(argv) {
	const [file, component, channel, installed, skipped] = argv;
	const text = $.NSString.stringWithContentsOfFileEncodingError(file, $.NSUTF8StringEncoding, null).js;
	const assetPrefix = component === 'web' ? 'aidetector-web-osx-' : 'aidetector-osx-';
	const current = installed ? parse(installed) : null;
	const skip = (skipped || '').split(/\s+/).filter(Boolean);
	let best = null;
	for (const release of JSON.parse(text)) {
		const tag = release.tag_name || '';
		if (release.draft || !tag.startsWith(component + '/')) continue;
		const version = tag.slice(component.length + 1);
		const parsed = parse(version);
		if (!parsed || skip.includes(version)) continue;
		if (channel !== 'beta' && parsed.pre.length) continue;
		if (current && compare(parsed, current) <= 0) continue;
		if (best && compare(parsed, best.parsed) <= 0) continue;
		const assets = (release.assets || []).filter((asset) => asset.state === 'uploaded');
		const zip = assets.find((asset) => asset.name === assetPrefix + version + '.zip');
		if (!zip) continue;
		const script = assets.find((asset) => asset.name === 'cowcatcher.sh');
		best = {
			parsed,
			line: [
				version,
				zip.browser_download_url,
				(zip.digest || '-').replace(/^sha256:/, ''),
				script ? script.browser_download_url : '-',
			].join(' '),
		};
	}
	return best ? best.line : '';
}
EOF
)

fetch_releases() {
	curl -fsSL --retry 3 --max-time 60 \
		-H "Accept: application/vnd.github+json" \
		-o "$1" "https://api.github.com/repos/$REPO/releases?per_page=100"
}

pick_release() {
	osascript -l JavaScript -e "$PICK_RELEASE_JS" "$1" "$2" "$CHANNEL" \
		"$(installed_version "$2")" "$(cat "$(skip_file_of "$2")" 2>/dev/null || true)"
}

# --- launchd -------------------------------------------------------------------

xml_escape() {
	local text="$1"
	text="${text//&/&amp;}"
	text="${text//</&lt;}"
	text="${text//>/&gt;}"
	printf '%s' "$text"
}

write_service_plist() {
	local component="$1" environment=""
	case "$component" in
	detector)
		environment="<key>PYTHONUNBUFFERED</key><string>1</string>"
		;;
	web)
		environment="<key>PORT</key><string>$WEB_PORT</string>
		<key>OPEN_BROWSER</key><string>false</string>"
		;;
	esac
	cat >"$(plist_of "$component")" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$(label_of "$component")</string>
	<key>ProgramArguments</key>
	<array>
		<string>$(xml_escape "$(binary_of "$component")")</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$(xml_escape "$APP_DIR")</string>
	<key>EnvironmentVariables</key>
	<dict>
		$environment
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>10</integer>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>StandardOutPath</key>
	<string>$(xml_escape "$(log_of "$component")")</string>
	<key>StandardErrorPath</key>
	<string>$(xml_escape "$(log_of "$component")")</string>
</dict>
</plist>
EOF
}

write_updater_plist() {
	cat >"$(plist_of updater)" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$(label_of updater)</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>$(xml_escape "$STATE_DIR/cowcatcher.sh")</string>
		<string>update</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>COWCATCHER_DIR</key>
		<string>$(xml_escape "$APP_DIR")</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>StartInterval</key>
	<integer>$CHECK_INTERVAL</integer>
	<key>ProcessType</key>
	<string>Background</string>
	<key>StandardOutPath</key>
	<string>$(xml_escape "$(log_of updater)")</string>
	<key>StandardErrorPath</key>
	<string>$(xml_escape "$(log_of updater)")</string>
</dict>
</plist>
EOF
}

service_loaded() {
	launchctl print "gui/$UID/$(label_of "$1")" >/dev/null 2>&1
}

service_pid() {
	launchctl print "gui/$UID/$(label_of "$1")" 2>/dev/null |
		awk '$1 == "pid" && $2 == "=" { print $3; exit }'
}

start_service() {
	if service_loaded "$1"; then
		launchctl kickstart -k "gui/$UID/$(label_of "$1")"
	else
		launchctl bootstrap "gui/$UID" "$(plist_of "$1")"
	fi
}

stop_service() {
	service_loaded "$1" || return 0
	launchctl bootout "gui/$UID/$(label_of "$1")" 2>/dev/null || true
	# bootout returns before the process has stopped; a bootstrap right after
	# it would fail.
	for _ in $(seq 30); do
		service_loaded "$1" || return 0
		sleep 1
	done
}

# Whether a freshly started component stays up. log_offset is the size of its
# log before the start, so only the new lines count.
check_health() {
	local component="$1" log_offset="$2" pid="" new_lines
	for _ in $(seq 30); do
		pid=$(service_pid "$component")
		[ -n "$pid" ] && break
		sleep 1
	done
	if [ -z "$pid" ]; then
		log "$component: start niet"
		return 1
	fi
	sleep "$HEALTH_SECONDS"
	if [ "$(service_pid "$component")" != "$pid" ]; then
		log "$component: stopte binnen $HEALTH_SECONDS seconden"
		return 1
	fi
	new_lines=$(tail -c "+$((log_offset + 1))" "$(log_of "$component")" 2>/dev/null || true)
	case "$component" in
	detector)
		# The detector restarts itself in the same process after a crash, so
		# the process staying up is not enough.
		if grep -q "Application crashed" <<<"$new_lines"; then
			log "detector: crasht na het starten"
			return 1
		fi
		if grep -q "starts again as soon as it is saved" <<<"$new_lines"; then
			log "detector: de nieuwe versie keurt config.json af"
			return 1
		fi
		;;
	web)
		if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:$WEB_PORT/")" = 000 ]; then
			log "web: geeft geen antwoord op poort $WEB_PORT"
			return 1
		fi
		;;
	esac
	return 0
}

# --- updating ------------------------------------------------------------------

take_lock() {
	local lock="$STATE_DIR/lock" pid
	if ! mkdir "$lock" 2>/dev/null; then
		pid=$(cat "$lock/pid" 2>/dev/null || true)
		[ "$pid" = $$ ] && return 0
		if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
			log "Er loopt al een update (pid $pid)"
			exit 0
		fi
		rm -rf "$lock"
		mkdir "$lock" || die "kan $lock niet maken"
	fi
	echo $$ >"$lock/pid"
	trap 'rm -rf "$STATE_DIR/lock"' EXIT
}

# Keeps the end of logs that grew too big; launchd keeps the file open, so the
# file is truncated in place instead of replaced.
trim_logs() {
	local file
	for file in "$LOG_DIR"/*.log; do
		[ -f "$file" ] || continue
		if [ "$(file_size "$file")" -gt "$LOG_MAX_BYTES" ]; then
			tail -c "$LOG_KEEP_BYTES" "$file" >"$file.tmp" && cat "$file.tmp" >"$file"
			rm -f "$file.tmp"
		fi
	done
}

# Puts the downloaded binary in place, restarts the service and puts the
# previous binary back when the new one does not stay up.
install_binary() {
	local component="$1" version="$2" new="$3"
	local binary previous log_offset
	binary=$(binary_of "$component")
	previous="$binary.previous"
	if [ -f "$binary" ]; then
		ln -f "$binary" "$previous" || return 1
		cp -f "$(version_file_of "$component")" "$(version_file_of "$component").previous" 2>/dev/null || true
	fi
	mv -f "$new" "$binary" || return 1
	echo "$version" >"$(version_file_of "$component")"

	log_offset=$(file_size "$(log_of "$component")")
	start_service "$component" || log "$component: launchctl kon de service niet starten"
	if check_health "$component" "$log_offset"; then
		log "$component: $version draait"
		return 0
	fi
	if [ ! -f "$previous" ]; then
		log "$component: $version draait niet goed, en er is geen vorige versie om terug te zetten"
		return 1
	fi
	echo "$version" >>"$(skip_file_of "$component")"
	restore_previous "$component"
	log "$component: $version overgeslagen, terug naar $(installed_version "$component")"
	return 1
}

restore_previous() {
	local component="$1" binary
	binary=$(binary_of "$component")
	mv -f "$binary.previous" "$binary" || return 1
	mv -f "$(version_file_of "$component").previous" "$(version_file_of "$component")" 2>/dev/null ||
		rm -f "$(version_file_of "$component")"
	start_service "$component"
}

update_component() {
	local component="$1" releases="$2"
	local pick version url sha256 script work zip new
	pick=$(pick_release "$releases" "$component") || {
		log "$component: releases niet te lezen"
		return 1
	}
	[ -n "$pick" ] || return 0
	read -r version url sha256 script <<<"$pick"
	log "$component: ${version} downloaden (nu: $(installed_version "$component"))"

	work=$(mktemp -d "$STATE_DIR/download.XXXXXX") || return 1
	zip="$work/release.zip"
	if ! curl -fsSL --retry 3 --max-time 1800 -o "$zip" "$url"; then
		log "$component: downloaden mislukt"
		rm -rf "$work"
		return 1
	fi
	if [ "$sha256" != - ] && [ "$(shasum -a 256 "$zip" | cut -d ' ' -f 1)" != "$sha256" ]; then
		log "$component: download is beschadigd (sha256 klopt niet)"
		rm -rf "$work"
		return 1
	fi
	if ! ditto -x -k "$zip" "$work/unzipped"; then
		log "$component: uitpakken mislukt"
		rm -rf "$work"
		return 1
	fi
	new=$(find "$work/unzipped" -type f -name '*.command' | head -n 1)
	if [ -z "$new" ]; then
		log "$component: geen programma in de zip"
		rm -rf "$work"
		return 1
	fi
	chmod +x "$new"
	xattr -c "$new" 2>/dev/null || true

	install_binary "$component" "$version" "$new"
	local result=$?
	rm -rf "$work"
	if [ "$result" = 0 ] && [ "$component" = detector ] && [ "$script" != - ]; then
		update_self "$script"
	fi
	return "$result"
}

# The updater comes along with each detector release, so changes to this
# script reach the Mac mini as well.
update_self() {
	local url="$1" current="$STATE_DIR/cowcatcher.sh" new="$STATE_DIR/cowcatcher.sh.new"
	if ! curl -fsSL --retry 3 --max-time 60 -o "$new" "$url"; then
		log "updater: nieuwe versie van het script niet te downloaden"
		rm -f "$new"
		return 1
	fi
	if cmp -s "$new" "$current"; then
		rm -f "$new"
		return 0
	fi
	if ! bash -n "$new" || ! grep -q '^REPO="StefHarmens/ai-detector"$' "$new"; then
		log "updater: nieuw script klopt niet, oude blijft"
		rm -f "$new"
		return 1
	fi
	chmod +x "$new"
	mv -f "$new" "$current"
	log "updater: script bijgewerkt"
}

command_update() {
	mkdir -p "$STATE_DIR" "$LOG_DIR"
	take_lock
	trim_logs
	rm -rf "$STATE_DIR"/download.*
	local releases="$STATE_DIR/releases.json" component failed=0
	if ! fetch_releases "$releases"; then
		log "GitHub niet bereikbaar, volgende keer opnieuw"
		return 1
	fi
	for component in $COMPONENTS; do
		update_component "$component" "$releases" || failed=1
	done
	return "$failed"
}

# --- install -------------------------------------------------------------------

# Prints the top-level names on the Desktop that config.json points at.
CONFIG_JS=$(
	cat <<'EOF'
ObjC.import('Foundation');

function read(file) {
	return $.NSString.stringWithContentsOfFileEncodingError(file, $.NSUTF8StringEncoding, null).js;
}

function walk(value, visit) {
	if (typeof value === 'string') return visit(value);
	if (Array.isArray(value)) return value.map((item) => walk(item, visit));
	if (value && typeof value === 'object') {
		const result = {};
		for (const key of Object.keys(value)) result[key] = walk(value[key], visit);
		return result;
	}
	return value;
}

function under(path, folder) {
	return path === folder || path.startsWith(folder + '/');
}

// entries <config> <desktop>: names on the Desktop that config.json uses.
// rewrite <config> <from> <to> [<from> <to> ...]: first matching folder wins.
function run(argv) {
	const [mode, file, ...rest] = argv;
	const config = JSON.parse(read(file));
	if (mode === 'entries') {
		const names = new Set();
		walk(config, (text) => {
			if (text.startsWith(rest[0] + '/')) names.add(text.slice(rest[0].length + 1).split('/')[0]);
			return text;
		});
		return [...names].filter(Boolean).join('\n');
	}
	const rewritten = walk(config, (text) => {
		for (let i = 0; i + 1 < rest.length; i += 2) {
			if (under(text, rest[i])) return rest[i + 1] + text.slice(rest[i].length);
		}
		return text;
	});
	const json = $.NSString.alloc.initWithUTF8String(JSON.stringify(rewritten, null, '\t') + '\n');
	if (!json.writeToFileAtomicallyEncodingError(file, true, $.NSUTF8StringEncoding, null)) {
		throw new Error('cannot write ' + file);
	}
	return '';
}
EOF
)

config_js() {
	osascript -l JavaScript -e "$CONFIG_JS" "$@"
}

# macOS does not let a background service read the Desktop without a
# permission that is lost with every new binary, so everything moves to
# APP_DIR: the old folder's files, plus whatever config.json uses on the
# Desktop (data, video, koeienlijst). config.json is rewritten to match.
migrate() {
	local old="$1" desktop="$HOME/Desktop" item name entries entry
	echo
	echo "Overzetten uit: $old"
	for item in "$old"/* "$old"/.[!.]*; do
		[ -e "$item" ] || continue
		name=$(basename "$item")
		case "$name" in
		*.command | .DS_Store) continue ;;
		esac
		if [ -e "$APP_DIR/$name" ]; then
			echo "  $name staat al in $APP_DIR, overgeslagen"
		else
			mv "$item" "$APP_DIR/$name" || die "kan $item niet verplaatsen"
			echo "  $name"
		fi
	done

	if [ -f "$APP_DIR/config.json" ]; then
		entries=$(config_js entries "$APP_DIR/config.json" "$desktop") || die "config.json is niet te lezen"
		while IFS= read -r entry; do
			[ -n "$entry" ] || continue
			[ "$desktop/$entry" = "$old" ] && continue
			if [ -e "$APP_DIR/$entry" ]; then
				echo "  $entry staat al in $APP_DIR, overgeslagen"
			elif [ -e "$desktop/$entry" ]; then
				mv "$desktop/$entry" "$APP_DIR/$entry" || die "kan $desktop/$entry niet verplaatsen"
				echo "  $entry (van het Bureaublad)"
			fi
		done <<<"$entries"
		cp "$APP_DIR/config.json" "$APP_DIR/config.json.voor-installatie"
		config_js rewrite "$APP_DIR/config.json" "$old" "$APP_DIR" "$desktop" "$APP_DIR" >/dev/null ||
			die "config.json kon niet aangepast worden"
		echo "  config.json wijst nu naar $APP_DIR (origineel: config.json.voor-installatie)"
	fi

	mv "$old" "$old (oud)" && echo "  De oude map heet nu \"$(basename "$old") (oud)\"; die kan weg als alles werkt."
}

# CowCatcher started by hand from Terminal would run twice next to the
# services and take port $WEB_PORT.
stop_manual_instances() {
	local running
	running=$(pgrep -fl 'aidetector-osx-|aidetector-web-osx-' || true)
	[ -n "$running" ] || return 0
	echo
	echo "CowCatcher draait nog los in Terminal:"
	echo "$running" | sed 's/^/  /'
	confirm "Deze stoppen? De services nemen het over." || die "stop ze eerst zelf (ctrl-C in Terminal)"
	pkill -f 'aidetector-osx-|aidetector-web-osx-' || true
	sleep 3
}

command_install() {
	local old="" arg
	while [ $# -gt 0 ]; do
		arg="$1"
		shift
		case "$arg" in
		--from) old="${1:-}" && shift ;;
		--stable) CHANNEL=stable ;;
		--beta) CHANNEL=beta ;;
		--port) WEB_PORT="${1:-}" && shift ;;
		--yes) ASSUME_YES=1 ;;
		*) die "onbekende optie: $arg" ;;
		esac
	done
	[ "$(uname -s)" = Darwin ] || die "dit script is voor macOS"
	[ "$(uname -m)" = arm64 ] || die "de macOS-builds zijn voor Apple silicon (arm64)"
	if [ -z "$old" ] && [ -d "$HOME/Desktop/CowCatcher - Custom" ]; then
		old="$HOME/Desktop/CowCatcher - Custom"
	fi
	old="${old%/}"
	if [ -n "$old" ] && [ ! -d "$old" ]; then
		die "map niet gevonden: $old"
	fi

	echo "CowCatcher installeren in $APP_DIR"
	echo "  - detector en web draaien als achtergrondservice en starten vanzelf na een herstart"
	echo "  - elke $((CHECK_INTERVAL / 60)) minuten wordt gekeken of er een nieuwe versie is (kanaal: $CHANNEL)"
	[ -n "$old" ] && echo "  - de bestanden uit \"$old\" verhuizen mee"
	confirm "Doorgaan?" || exit 1

	stop_manual_instances
	mkdir -p "$APP_DIR" "$STATE_DIR" "$LOG_DIR" "$AGENTS_DIR" || die "kan $APP_DIR niet maken"
	[ -n "$old" ] && migrate "$old"
	[ -f "$APP_DIR/config.json" ] || echo "Let op: er is nog geen config.json in $APP_DIR"

	cp -f "${BASH_SOURCE[0]}" "$STATE_DIR/cowcatcher.sh.new" && mv -f "$STATE_DIR/cowcatcher.sh.new" "$STATE_DIR/cowcatcher.sh"
	chmod +x "$STATE_DIR/cowcatcher.sh"
	printf 'CHANNEL=%s\nWEB_PORT=%s\n' "$CHANNEL" "$WEB_PORT" >"$SETTINGS_FILE"
	if [ ! -e "$HOME/Desktop/CowCatcher" ]; then
		ln -s "$APP_DIR" "$HOME/Desktop/CowCatcher"
	fi

	# Reinstalling picks up changed settings in the service definitions.
	local component
	for component in $COMPONENTS updater; do
		stop_service "$component"
	done
	for component in $COMPONENTS; do
		write_service_plist "$component"
		if [ -f "$(binary_of "$component")" ]; then
			start_service "$component"
		fi
	done
	write_updater_plist

	echo
	echo "Nieuwste versies downloaden en starten (detector is ~300 MB, en elke versie"
	echo "krijgt $HEALTH_SECONDS seconden om te laten zien dat hij goed draait)..."
	command_update
	trap - EXIT
	rm -rf "$STATE_DIR/lock"
	start_service updater

	echo
	command_status
	echo
	echo "Klaar. Handig om te onthouden:"
	echo "  $STATE_DIR/cowcatcher.sh status"
	echo "  $STATE_DIR/cowcatcher.sh logs detector"
	echo "Zet in Systeeminstellingen > Gebruikers en groepen 'automatisch inloggen' aan,"
	echo "dan start CowCatcher ook vanzelf na een stroomstoring."
}

# --- other commands --------------------------------------------------------

command_status() {
	local component pid version previous skipped
	echo "CowCatcher in $APP_DIR (kanaal: $CHANNEL)"
	for component in $COMPONENTS; do
		version=$(installed_version "$component")
		pid=$(service_pid "$component")
		previous=$(cat "$(version_file_of "$component").previous" 2>/dev/null || true)
		printf '  %-9s %-22s %s%s\n' "$component" "${version:-niet geïnstalleerd}" \
			"$([ -n "$pid" ] && echo "draait (pid $pid)" || echo "draait niet")" \
			"${previous:+, vorige: $previous}"
		skipped=$(cat "$(skip_file_of "$component")" 2>/dev/null | tr '\n' ' ')
		[ -n "$skipped" ] && echo "            overgeslagen: $skipped"
	done
	if service_loaded updater; then
		echo "  updater   kijkt elke $((CHECK_INTERVAL / 60)) minuten"
	else
		echo "  updater   staat uit"
	fi
	if [ -s "$(log_of updater)" ]; then
		echo
		echo "Laatste meldingen van de updater:"
		tail -n 5 "$(log_of updater)" | sed 's/^/  /'
	fi
}

command_logs() {
	local component="${1:-detector}"
	case "$component" in
	detector | web | updater) ;;
	*) die "kies detector, web of updater" ;;
	esac
	tail -n 100 -f "$(log_of "$component")"
}

command_rollback() {
	local component="${1:-}" version
	case "$component" in
	detector | web) ;;
	*) die "kies detector of web" ;;
	esac
	[ -f "$(binary_of "$component").previous" ] || die "er is geen vorige versie van $component"
	take_lock
	version=$(installed_version "$component")
	[ -n "$version" ] && echo "$version" >>"$(skip_file_of "$component")"
	restore_previous "$component" || die "terugzetten mislukt"
	log "$component: $version teruggezet naar $(installed_version "$component"); $version wordt overgeslagen"
}

command_start_stop() {
	local action="$1" component="${2:-}"
	case "$component" in
	detector | web) ;;
	*) die "kies detector of web" ;;
	esac
	if [ "$action" = stop ]; then
		stop_service "$component"
		echo "$component is gestopt; hij start weer met: $0 start $component, of na een herstart van de Mac"
	else
		start_service "$component" && echo "$component is gestart"
	fi
}

command_uninstall() {
	local component
	for component in updater $COMPONENTS; do
		stop_service "$component"
		rm -f "$(plist_of "$component")"
	done
	echo "Services gestopt en verwijderd. Alle bestanden staan nog in $APP_DIR."
}

main() {
	load_settings
	local command="${1:-help}"
	[ $# -gt 0 ] && shift
	case "$command" in
	install) command_install "$@" ;;
	update) command_update ;;
	status) command_status ;;
	logs) command_logs "$@" ;;
	rollback) command_rollback "$@" ;;
	start | stop) command_start_stop "$command" "$@" ;;
	uninstall) command_uninstall ;;
	*) sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
	esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
	main "$@"
fi
