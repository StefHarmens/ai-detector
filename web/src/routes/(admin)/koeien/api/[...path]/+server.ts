import { detectorApiUrl } from '$lib/server/detector-api';
import type { RequestHandler } from './$types';

const UNREACHABLE =
	'De detector is niet bereikbaar. Draait hij op deze computer, en staat "api" niet op null in config.json?';

const forward: RequestHandler = async ({ params, request, url }) => {
	const target = `${detectorApiUrl()}/api/${params.path}${url.search}`;
	let response: Response;
	try {
		response = await fetch(target, {
			method: request.method,
			headers: { 'Content-Type': 'application/json' },
			body: request.method === 'GET' ? undefined : await request.text(),
			signal: AbortSignal.timeout(30_000)
		});
	} catch {
		return Response.json({ error: UNREACHABLE }, { status: 502 });
	}
	return new Response(response.body, {
		status: response.status,
		headers: {
			'Content-Type': response.headers.get('Content-Type') ?? 'application/octet-stream',
			'Cache-Control': response.headers.get('Cache-Control') ?? 'no-store'
		}
	});
};

export const GET = forward;
export const POST = forward;
export const DELETE = forward;
