// Reads the warnings and errors from the log files the macOS services write
// (see macos/cowcatcher.sh), so they can be seen on the web page instead of
// in a Terminal on the Mac mini.
import { existsSync } from 'node:fs';
import { open, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';

export type LogLevel = 'error' | 'warning' | 'info';
export type LogSource = 'detector' | 'updater';

export interface LogEntry {
	source: LogSource;
	level: LogLevel;
	// Local time as written in the log, e.g. "2026-10-03T18:07:20".
	time: string;
	// The Python module, e.g. "aidetector.sources.hires"; null for the updater.
	logger: string | null;
	message: string;
	// A traceback or other lines that belong to the message.
	details: string | null;
}

export interface LogPage {
	// Null when the log folder does not exist: CowCatcher is not running as
	// the services of the install script.
	directory: string | null;
	entries: LogEntry[];
	// When the farmer last cleared the Logboek ("2026-10-04T13:05:00"); only
	// what came after it is shown. The log files themselves are kept.
	cleared: string | null;
}

// Next to the logs; not a .log file, so the updater leaves it alone.
const CLEARED_FILE = 'logboek-gewist.txt';

// The detector logs every detection, so 16 MB is about a day and a half;
// the services keep at most 50 MB per file.
const TAIL_BYTES = 16 * 1024 * 1024;
const MAX_ENTRIES = 500;
const MAX_DETAILS = 20_000;

export function logDirectory(): string {
	return process.env.COWCATCHER_LOG_DIR || path.join(homedir(), 'Library', 'Logs', 'CowCatcher');
}

// The page has no password, so stream keys and bot tokens never show.
export function hideSecrets(text: string): string {
	return text
		.replace(/(rtsps?:\/\/[^/\s"']+\/)[^\s"'|,)\]]+/gi, '$1<key>')
		.replace(/bot\d+:[\w-]+/g, 'bot<token>');
}

// "2026-10-03 18:07:20,195 - aidetector.sources.hires - WARNING - High-resolution ..."
const DETECTOR_LINE =
	/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d{3} - (\S+) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$/;

export function parseDetectorLog(text: string): LogEntry[] {
	const entries: LogEntry[] = [];
	// Lines without a time (a traceback) belong to the entry above, if kept.
	let current: LogEntry | null = null;
	for (const line of text.split('\n')) {
		const match = DETECTOR_LINE.exec(line);
		if (!match) {
			if (current && line.trim()) {
				const details = current.details ? `${current.details}\n${line}` : line;
				current.details = details.slice(0, MAX_DETAILS);
			}
			continue;
		}
		const [, date, clock, logger, level, message] = match;
		current = null;
		if (level === 'WARNING' || level === 'ERROR' || level === 'CRITICAL') {
			current = {
				source: 'detector',
				level: level === 'WARNING' ? 'warning' : 'error',
				time: `${date}T${clock}`,
				logger,
				message: hideSecrets(message),
				details: null
			};
			entries.push(current);
		} else if (message.startsWith('Starting application')) {
			// The rest of this line is the whole config, stream links included.
			entries.push({
				source: 'detector',
				level: 'info',
				time: `${date}T${clock}`,
				logger,
				message: 'Detector gestart',
				details: null
			});
		}
	}
	for (const entry of entries) {
		if (entry.details) entry.details = hideSecrets(entry.details);
	}
	return entries;
}

// "2026-10-03 15:26:48 web: v0.8.0-beta.8 draait"
const UPDATER_LINE = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)$/;
// The updater's Dutch words for something that went wrong.
const UPDATER_PROBLEM = /overgeslagen|mislukt|beschadigd|niet|klopt|kon |fout/i;

export function parseUpdaterLog(text: string): LogEntry[] {
	const entries: LogEntry[] = [];
	for (const line of text.split('\n')) {
		const match = UPDATER_LINE.exec(line);
		if (!match) continue;
		const [, date, clock, message] = match;
		entries.push({
			source: 'updater',
			level: UPDATER_PROBLEM.test(message) ? 'warning' : 'info',
			time: `${date}T${clock}`,
			logger: null,
			message: hideSecrets(message),
			details: null
		});
	}
	return entries;
}

async function readTail(file: string, bytes = TAIL_BYTES): Promise<string> {
	let size: number;
	try {
		size = (await stat(file)).size;
	} catch {
		return '';
	}
	const start = Math.max(0, size - bytes);
	const handle = await open(file, 'r');
	try {
		const buffer = Buffer.alloc(size - start);
		await handle.read(buffer, 0, buffer.length, start);
		const text = buffer.toString('utf8');
		// Reading from the middle of the file starts halfway a line.
		return start > 0 ? text.slice(text.indexOf('\n') + 1) : text;
	} finally {
		await handle.close();
	}
}

export async function readLogs(directory = logDirectory()): Promise<LogPage> {
	if (!existsSync(directory)) return { directory: null, entries: [], cleared: null };
	const [detector, updater, cleared] = await Promise.all([
		readTail(path.join(directory, 'detector.log')),
		readTail(path.join(directory, 'updater.log')),
		readCleared(directory)
	]);
	const entries = [...parseDetectorLog(detector), ...parseUpdaterLog(updater)]
		.filter((entry) => cleared === null || entry.time > cleared)
		// Newest first; the same second keeps the order of the log.
		.map((entry, index) => ({ entry, index }))
		.sort((a, b) => b.entry.time.localeCompare(a.entry.time) || b.index - a.index)
		.slice(0, MAX_ENTRIES)
		.map(({ entry }) => entry);
	return { directory, entries, cleared };
}

const LOCAL_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/;

async function readCleared(directory: string): Promise<string | null> {
	try {
		const text = (await readFile(path.join(directory, CLEARED_FILE), 'utf8')).trim();
		return LOCAL_TIME.test(text) ? text : null;
	} catch {
		return null;
	}
}

// Local time as the logs write it, so it compares with their entries.
export function localTime(date = new Date()): string {
	const pad = (value: number) => String(value).padStart(2, '0');
	return (
		`${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
		`T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
	);
}

// Hides everything up to now on the Logboek page, or (undo) shows it again.
export async function clearLogs(clear: boolean, directory = logDirectory()): Promise<void> {
	const file = path.join(directory, CLEARED_FILE);
	if (clear) await writeFile(file, `${localTime()}\n`);
	else await rm(file, { force: true });
}
