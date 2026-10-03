import { readLogs } from '$lib/server/logs';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async () =>
	Response.json(await readLogs(), { headers: { 'Cache-Control': 'no-store' } });
