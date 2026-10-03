import { detectionFolders } from '$lib/server/detection-folders';
import { resolveWithinDirectory } from '$lib/server/shared-paths';
import { error } from '@sveltejs/kit';
import fs from 'node:fs/promises';
import path from 'node:path';

const contentTypes: Record<string, string> = {
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.png': 'image/png',
	'.gif': 'image/gif',
	'.webp': 'image/webp',
	'.bmp': 'image/bmp',
	'.svg': 'image/svg+xml',
	'.mp4': 'video/mp4',
	'.webm': 'video/webm',
	'.mov': 'video/quicktime',
	'.json': 'application/json'
};

export async function GET({ params }) {
	const folder = (await detectionFolders()).get(params.category);
	const resolvedPath = folder
		? resolveWithinDirectory(folder, [params.stage, params.timestamp, params.resource].join('/'))
		: null;
	if (!resolvedPath) {
		error(404, 'Niet gevonden');
	}

	const file = await fs.readFile(resolvedPath).catch(() => error(404, 'Niet gevonden'));
	const extension = path.extname(resolvedPath).toLowerCase();

	return new Response(file, {
		headers: {
			'Content-Type': contentTypes[extension] ?? 'application/octet-stream',
			'Cache-Control': 'public, max-age=30'
		}
	});
}
