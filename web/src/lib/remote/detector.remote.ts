import { command, query } from '$app/server';
import { getConfig, getConfigSchema } from './config.remote';
import * as v from 'valibot';
import type { DetectorConfig } from '$lib/schema';
import { saveConfig } from '$lib/server/shared-paths';

export const getDetectorPresets = query(async () => {
	const response = await fetch(
		'https://api.github.com/repos/StefHarmens/ai-detector/contents/config/detector',
		{
			headers: {
				Accept: 'application/vnd.github+json',
				'User-Agent': 'ai-detector-web'
			}
		}
	);
	const items: { name: string }[] = await response.json();
	return items.map((item) => item.name);
});

export const getDetectorPreset = query(
	v.object({
		file: v.string()
	}),
	async ({ file }): Promise<DetectorConfig> => {
		return await fetch(
			`https://raw.githubusercontent.com/StefHarmens/ai-detector/main/config/detector/${file}`
		).then((response) => response.json());
	}
);

export const getDetectorSchema = query(async () => {
	const configSchema = await getConfigSchema();

	return {
		$defs: configSchema.$defs,
		...(configSchema.$defs.DetectorConfig as Record<string, unknown>)
	};
});

export const getDetectors = query(async () => {
	const { config, app } = await getConfig();

	const detectorZip = config.detectors.map((detector, index) => {
		return { detector, meta: app.detectors[index] };
	});
	return detectorZip;
});

export const getDetector = query(
	v.object({
		label: v.string()
	}),
	async ({ label }) => {
		const detectors = await getDetectors();
		return detectors.find((detector) => detector.meta.label === label);
	}
);

export const saveDetector = command(
	v.object({
		original: v.optional(v.string()),
		detector: v.any(),
		meta: v.object({
			label: v.string()
		})
	}),
	async ({ original, detector, meta }) => {
		const { config, app } = await getConfig();
		if (original) {
			const index = app.detectors.findIndex((detector) => detector.label === original);
			config.detectors[index] = detector;
			app.detectors[index] = meta;
		} else {
			const lengthDiff = config.detectors.length - app.detectors.length;
			for (let i = 0; i < lengthDiff; i++) {
				app.detectors.push({ label: 'Detector ' + (app.detectors.length + 1) });
			}
			config.detectors.push(detector);
			app.detectors.push(meta);
		}
		await saveConfig({ config, app });
	}
);

export const deleteDetector = command(
	v.object({
		label: v.string()
	}),
	async ({ label }) => {
		const { config, app } = await getConfig();
		const index = app.detectors.findIndex((detector) => detector.label === label);
		config.detectors.splice(index, 1);
		app.detectors.splice(index, 1);
		await saveConfig({ config, app });
	}
);
