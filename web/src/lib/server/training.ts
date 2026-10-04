// What the Trainen page shows: how many checked examples there are, when the
// model was last trained, and the command that trains it again, with the
// paths of this Mac mini filled in from config.json.
import { existsSync } from 'node:fs';
import { readFile, readdir, stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { CONFIG_PATH } from './shared-paths';

const IMAGE = /\.(bmp|jpe?g|png|webp)$/i;
// The models CowCatcher ships, e.g. cowcatcherV17.pt; training starts from
// the newest one, not from an earlier training.
const BASE_MODEL = /^cowcatcherV(\d+)\.pt$/i;
const TRAINED_NAME = 'cowcatcher-feedback.pt';

export interface TrainingInfo {
	appDirectory: string;
	dataRoot: string;
	good: number;
	bad: number;
	// The model the detector uses now.
	model: string | null;
	// The model training starts from, and where the trained one goes.
	base: string | null;
	output: string;
	// When the trained model was made, and the examples added since then.
	trainedAt: string | null;
	newSince: number | null;
	command: string;
	// What keeps the command from working as it is.
	problems: string[];
}

type Detector = {
	yolo?: { model?: unknown };
	exporters?: {
		telegram?: unknown;
		disk?: unknown;
	};
};

function list(value: unknown): Record<string, unknown>[] {
	const items = Array.isArray(value) ? value : value ? [value] : [];
	return items.filter((item): item is Record<string, unknown> => typeof item === 'object');
}

function local(appDirectory: string, value: string): string {
	return path.resolve(appDirectory, value.replace(/^~(?=$|\/)/, homedir()));
}

// Where the good and bad examples are: next to the Telegram feedback, or
// next to the doubt folder of the disk exporter.
function dataRoot(appDirectory: string, detector: Detector | undefined): string {
	for (const chat of list(detector?.exporters?.telegram)) {
		if (typeof chat.feedback_directory === 'string') {
			return local(appDirectory, chat.feedback_directory);
		}
	}
	for (const disk of list(detector?.exporters?.disk)) {
		if (disk.review && typeof disk.directory === 'string') {
			return path.dirname(local(appDirectory, disk.directory));
		}
	}
	return path.join(appDirectory, 'data');
}

async function images(directory: string): Promise<{ count: number; newest: number[] }> {
	try {
		const names = (await readdir(directory)).filter((name) => IMAGE.test(name));
		const times = await Promise.all(
			names.map((name) =>
				stat(path.join(directory, name)).then(
					(info) => info.mtimeMs,
					() => 0
				)
			)
		);
		return { count: names.length, newest: times };
	} catch {
		return { count: 0, newest: [] };
	}
}

async function baseModel(modelsDirectory: string, model: string | null): Promise<string | null> {
	if (model && model.toLowerCase().endsWith('.pt') && !/feedback/i.test(path.basename(model))) {
		return model;
	}
	try {
		const shipped = (await readdir(modelsDirectory))
			.map((name) => ({ name, version: Number(BASE_MODEL.exec(name)?.[1] ?? NaN) }))
			.filter((item) => !Number.isNaN(item.version))
			.sort((a, b) => b.version - a.version);
		return shipped.length ? path.join(modelsDirectory, shipped[0].name) : null;
	} catch {
		return null;
	}
}

const quote = (value: string) => `"${value.replaceAll('"', '\\"')}"`;

export async function trainingInfo(configPath = CONFIG_PATH): Promise<TrainingInfo> {
	const appDirectory = path.dirname(configPath);
	const problems: string[] = [];
	let detector: Detector | undefined;
	try {
		const config = JSON.parse(await readFile(configPath, 'utf8'));
		detector = list(config?.detectors)[0] as Detector | undefined;
	} catch {
		problems.push(`config.json is niet te lezen (${configPath}).`);
	}
	const root = dataRoot(appDirectory, detector);
	const configured = typeof detector?.yolo?.model === 'string' ? detector.yolo.model : null;
	const model =
		configured && !/^https?:/i.test(configured) ? local(appDirectory, configured) : null;
	const modelsDirectory = model ? path.dirname(model) : path.join(appDirectory, 'models');
	const base = await baseModel(modelsDirectory, model);
	const output = path.join(modelsDirectory, TRAINED_NAME);
	if (!base) {
		problems.push(
			`Geen basismodel gevonden: zet een cowcatcherV…pt in ${modelsDirectory}, of vul bij --model het model in waarmee je wilt beginnen.`
		);
	}

	const [good, bad] = await Promise.all([
		images(path.join(root, 'good')),
		images(path.join(root, 'bad'))
	]);
	if (good.count === 0) problems.push(`Er staan nog geen goede voorbeelden in ${root}/good.`);
	if (bad.count === 0) problems.push(`Er staan nog geen foute voorbeelden in ${root}/bad.`);

	let trainedAt: string | null = null;
	let newSince: number | null = null;
	if (existsSync(output)) {
		const trained = (await stat(output)).mtimeMs;
		trainedAt = new Date(trained).toISOString();
		newSince = [...good.newest, ...bad.newest].filter((time) => time > trained).length;
	}

	const updater = path.join(appDirectory, 'updater', 'cowcatcher.sh');
	// One line per step: pasted as a whole, the detector starts again even
	// when the training fails.
	const command = [
		`${quote(updater)} stop detector`,
		`cd ${quote(appDirectory)} && ./aidetector train-feedback \\`,
		`  --config config.json \\`,
		`  --data-root ${quote(root)} \\`,
		`  --model ${quote(base ?? path.join(modelsDirectory, 'cowcatcherV17.pt'))} \\`,
		`  --output ${quote(output)} \\`,
		`  --epochs 25 --batch 4 --device mps --update-config`,
		`${quote(updater)} start detector`
	].join('\n');

	return {
		appDirectory,
		dataRoot: root,
		good: good.count,
		bad: bad.count,
		model,
		base,
		output,
		trainedAt,
		newSince,
		command,
		problems
	};
}
