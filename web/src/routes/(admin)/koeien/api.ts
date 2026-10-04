export interface Candidate {
	cow: string;
	label: string;
	score: number;
	photo: string | null;
	// Photos in her folder; too few keep her from being recognised.
	photos: number;
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

// When the detector fills in a cow without asking (cows.accept_score etc.).
export interface RecognitionRules {
	accept_score: number;
	accept_margin: number;
	min_photos: number;
}

export interface SightingPage {
	items: Sighting[];
	total: number;
	open: number;
	cameras: string[];
	rules: RecognitionRules;
}

export interface Cow {
	life_number: string;
	number: string | null;
	work_number: string | null;
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
	items: {
		cow: string;
		label: string;
		work_number: string | null;
		mounted: number;
		mounting: number;
	}[];
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
		// A photo goes as it is, everything else as JSON.
		const photo = body instanceof Blob;
		response = await fetch(`${BASE}/${path}`, {
			method,
			headers:
				body === undefined
					? undefined
					: {
							'Content-Type': photo ? body.type || 'application/octet-stream' : 'application/json'
						},
			body: body === undefined ? undefined : photo ? body : JSON.stringify(body)
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

// The cows the detector found on a photo, to pick the one to add.
export interface FoundCows {
	token: string;
	width: number;
	height: number;
	// Box as fractions of the photo: x1, y1, x2, y2.
	cows: { index: number; box: [number, number, number, number] }[];
}

export interface Camera {
	index: number;
	name: string;
}

export function foundPhoto(token: string): string {
	return `${BASE}/koeien/zoek/${token}.jpg`;
}

export function foundCowPhoto(token: string, index: number): string {
	return `${BASE}/koeien/zoek/${token}_${index}_foto.jpg`;
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
