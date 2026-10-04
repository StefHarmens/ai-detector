import { clearLogs, readLogs } from '$lib/server/logs';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async () =>
	Response.json(await readLogs(), { headers: { 'Cache-Control': 'no-store' } });

// {"wissen": true} hides what is in the Logboek now; false shows it again.
export const POST: RequestHandler = async ({ request }) => {
	const body = await request.json().catch(() => ({}));
	if (typeof body.wissen !== 'boolean') {
		return Response.json({ error: 'Zeg met "wissen" of het logboek leeg moet.' }, { status: 400 });
	}
	await clearLogs(body.wissen);
	return Response.json(await readLogs(), { headers: { 'Cache-Control': 'no-store' } });
};
