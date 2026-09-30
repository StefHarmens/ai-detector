export interface Candidate {
	cow: string;
	label: string;
	score: number;
	photo: string | null;
}

export interface Slot {
	slot: number;
	name: string;
	title: string;
	mounter: boolean;
	cow: string | null;
	label: string | null;
	how: 'auto' | 'boer' | null;
	bad_photo: boolean;
	score: number | null;
	candidates: Candidate[];
}

export interface Sighting {
	id: string;
	chat: string;
	date: string;
	camera: string;
	split: boolean;
	role_certain: boolean;
	false: boolean;
	split_wrong: boolean;
	open: boolean;
	photos: string[];
	video: boolean;
	slots: Slot[];
}

export interface SightingPage {
	items: Sighting[];
	total: number;
	open: number;
	cameras: string[];
}

export interface Cow {
	life_number: string;
	number: string | null;
	name: string | null;
	archived: string | null;
	label: string;
	photos: number;
	photo: string | null;
}

export interface CowList {
	items: Cow[];
	herd_file: string | null;
}

export interface Overview {
	days: number;
	mounts: number;
	unknown: number;
	items: { cow: string; label: string; mounted: number; mounting: number }[];
}

export class ApiError extends Error {
	constructor(
		message: string,
		readonly status: number
	) {
		super(message);
	}
}

const BASE = '/koeien/api';

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
	let response: Response;
	try {
		response = await fetch(`${BASE}/${path}`, {
			method,
			headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
	} catch {
		throw new ApiError('Geen verbinding met de website. Zit je op het wifi van de boerderij?', 0);
	}
	const data = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new ApiError(data.error ?? `Fout ${response.status}`, response.status);
	}
	return data as T;
}

export function sightingPhoto(id: string, name: string): string {
	return `${BASE}/sprongen/${id}/${name}.jpg`;
}

export function sightingVideo(id: string): string {
	return `${BASE}/sprongen/${id}/video.mp4`;
}

export function cowPhoto(lifeNumber: string, name: string): string {
	return `${BASE}/koeien/${encodeURIComponent(lifeNumber)}/fotos/${encodeURIComponent(name)}`;
}

export function percent(score: number | null): string {
	return score === null ? '' : `${Math.round(score * 100)}%`;
}

const dateFormatter = new Intl.DateTimeFormat('nl-NL', {
	weekday: 'short',
	day: 'numeric',
	month: 'short',
	hour: '2-digit',
	minute: '2-digit'
});

export function formatDate(value: string): string {
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date);
}
