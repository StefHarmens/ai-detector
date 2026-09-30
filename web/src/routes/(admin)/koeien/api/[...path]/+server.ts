import { detectorApiUrl } from '$lib/server/detector-api';
import type { RequestHandler } from './$types';

const UNREACHABLE =
	'De detector is niet bereikbaar. Draait hij op deze computer, en staat "api" niet op null in config.json?';

const forward: RequestHandler = async ({ params, request, url }) => {
	const target = `${detectorApiUrl()}/api/${params.path}${url.search}`;
	let response: Response;
	try {
		const headers: Record<string, string> = { 'Content-Type': 'application/json' };
		// Browsers fetch a video in parts; Safari plays none without it.
		const range = request.headers.get('Range');
		if (range) headers.Range = range;
		response = await fetch(target, {
			method: request.method,
			headers,
			body: request.method === 'GET' ? undefined : await request.text(),
			signal: AbortSignal.timeout(30_000)
		});
	} catch {
		return Response.json({ error: UNREACHABLE }, { status: 502 });
	}
	const headers = new Headers({
		'Content-Type': response.headers.get('Content-Type') ?? 'application/octet-stream',
		'Cache-Control': response.headers.get('Cache-Control') ?? 'no-store'
	});
	for (const name of ['Content-Length', 'Content-Range', 'Accept-Ranges']) {
		const value = response.headers.get(name);
		if (value) headers.set(name, value);
	}
	return new Response(response.body, { status: response.status, headers });
};

export const GET = forward;
export const POST = forward;
export const DELETE = forward;
