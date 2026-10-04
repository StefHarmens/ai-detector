<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import CheckIcon from '@lucide/svelte/icons/check';
	import XIcon from '@lucide/svelte/icons/x';
	import { toast } from 'svelte-sonner';
	import { api, formatDate } from '../koeien/api';

	type Decision = 'good' | 'bad' | 'skip' | null;

	interface Doubt {
		folder: number;
		name: string;
		date: string | null;
		camera: string | null;
		confidence: number | null;
		duration: number | null;
		detections: number | null;
		decision: Decision;
		video: boolean;
	}

	interface DoubtPage {
		items: Doubt[];
		total: number;
		counts: { open: number; good: number; bad: number; skip: number };
	}

	const PAGE_SIZE = 12;
	const labels = { good: 'Goed', bad: 'Fout', skip: 'Overgeslagen' } as const;

	let filter = $state<'open' | 'alles'>('open');
	let items = $state<Doubt[]>([]);
	let total = $state(0);
	let counts = $state<DoubtPage['counts'] | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let version = 0;

	function file(item: Doubt, resource: string) {
		return `/koeien/api/twijfel/${item.folder}/${encodeURIComponent(item.name)}/${resource}`;
	}

	async function load(reset: boolean) {
		const current = reset ? ++version : version;
		loading = true;
		error = null;
		try {
			const offset = reset
				? 0
				: filter === 'open'
					? items.filter((item) => item.decision === null).length
					: items.length;
			const page = await api<DoubtPage>(
				`twijfel?filter=${filter}&offset=${offset}&limit=${PAGE_SIZE}`
			);
			if (current !== version) return;
			const known = new Set(items.map((item) => item.name));
			items = reset
				? page.items
				: [...items, ...page.items.filter((item) => !known.has(item.name))];
			total = page.total;
			counts = page.counts;
		} catch (err) {
			if (current === version) error = err instanceof Error ? err.message : String(err);
		} finally {
			if (current === version) loading = false;
		}
	}

	async function decide(item: Doubt, decision: Decision) {
		busy = item.name;
		try {
			const result = await api<{ message: string; decision: Decision }>(
				`twijfel/${item.folder}/${encodeURIComponent(item.name)}`,
				'POST',
				{ decision }
			);
			toast.success(result.message);
			if (counts) {
				counts[item.decision ?? 'open'] -= 1;
				counts[result.decision ?? 'open'] += 1;
			}
			if (filter === 'open')
				total += (result.decision === null ? 1 : 0) - (item.decision === null ? 1 : 0);
			// The card stays until the next reload, so the choice can be undone.
			items = items.map((other) =>
				other.name === item.name ? { ...other, decision: result.decision } : other
			);
		} catch (err) {
			toast.error(err instanceof Error ? err.message : 'Opslaan lukte niet');
		} finally {
			busy = null;
		}
	}

	const remaining = $derived(
		total -
			(filter === 'open' ? items.filter((item) => item.decision === null).length : items.length)
	);

	$effect(() => {
		filter;
		void load(true);
	});
</script>

<svelte:head>
	<title>Twijfel · CowCatcher</title>
</svelte:head>

<section class="space-y-6">
	<header class="space-y-1">
		<h1 class="text-2xl font-semibold tracking-tight">Twijfel</h1>
		<p class="max-w-3xl text-sm text-muted-foreground">
			Sprongen waar YOLO net niet zeker genoeg van was, of die te kort duurden. Ze gaven geen
			melding. Kijk de video en kies <b>Goed</b> als het een sprong is of <b>Fout</b> als het er
			geen is: ze gaan naar <span class="font-mono">data/good</span> of
			<span class="font-mono">data/bad</span>, klaar voor de volgende training, en verdwijnen uit de
			twijfel-map. Met <b>Keuze wissen</b> komen ze terug; na een maand worden beoordeelde gevallen opgeruimd
			(de foto voor het trainen blijft).
		</p>
	</header>

	<div class="flex flex-wrap items-center gap-2">
		<Button
			type="button"
			size="sm"
			variant={filter === 'open' ? 'default' : 'outline'}
			aria-pressed={filter === 'open'}
			onclick={() => (filter = 'open')}
		>
			Nog te doen{#if counts}&nbsp;({counts.open}){/if}
		</Button>
		<Button
			type="button"
			size="sm"
			variant={filter === 'alles' ? 'default' : 'outline'}
			aria-pressed={filter === 'alles'}
			onclick={() => (filter = 'alles')}
		>
			Alles
		</Button>
		{#if counts}
			<span class="text-sm text-muted-foreground">
				{counts.good} goed · {counts.bad} fout · {counts.skip} overgeslagen
			</span>
		{/if}
		<Button type="button" size="sm" variant="ghost" disabled={loading} onclick={() => load(true)}>
			Vernieuwen
		</Button>
	</div>

	{#if error}
		<p class="text-sm font-semibold text-destructive">{error}</p>
	{:else if items.length === 0 && !loading}
		<p class="text-sm text-muted-foreground">
			{filter === 'open' ? 'Alles is beoordeeld.' : 'Nog geen twijfelgevallen.'}
		</p>
	{/if}

	<div class="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
		{#each items as item (item.folder + ':' + item.name)}
			<Card.Root class={item.decision ? 'opacity-80' : ''}>
				<Card.Header class="gap-1">
					<Card.Title class="flex flex-wrap items-center gap-2 text-base">
						<span>{item.date ? formatDate(item.date) : item.name}</span>
						{#if item.camera}<span class="font-normal text-muted-foreground">· {item.camera}</span
							>{/if}
						{#if item.decision}
							<Badge
								variant={item.decision === 'bad' ? 'destructive' : 'default'}
								class={item.decision === 'good' ? 'bg-emerald-600 text-white' : ''}
							>
								{labels[item.decision]}
							</Badge>
						{/if}
					</Card.Title>
					<Card.Description>
						{#if item.confidence !== null}{Math.round(item.confidence * 100)}% zeker{/if}
						{#if item.duration !== null}· {item.duration.toFixed(1)} s{/if}
					</Card.Description>
				</Card.Header>
				<Card.Content>
					{#if item.video}
						<!-- svelte-ignore a11y_media_has_caption -->
						<video
							class="aspect-video w-full rounded-md bg-black"
							controls
							preload="none"
							playsinline
							poster={file(item, 'best.jpg')}
							src={file(item, 'video.mp4')}
						></video>
					{:else}
						<img
							class="aspect-video w-full rounded-md bg-black object-contain"
							src={file(item, 'best.jpg')}
							alt="Beeld van de twijfel"
							loading="lazy"
						/>
					{/if}
				</Card.Content>
				<Card.Footer class="flex flex-wrap gap-2">
					<Button
						type="button"
						disabled={busy === item.name}
						class="bg-emerald-600 text-white hover:bg-emerald-700"
						onclick={() => decide(item, 'good')}
					>
						<CheckIcon /> Goed
					</Button>
					<Button
						type="button"
						variant="destructive"
						disabled={busy === item.name}
						onclick={() => decide(item, 'bad')}
					>
						<XIcon /> Fout
					</Button>
					<Button
						type="button"
						variant="outline"
						disabled={busy === item.name}
						onclick={() => decide(item, 'skip')}
					>
						Weet niet
					</Button>
					{#if item.decision}
						<Button
							type="button"
							variant="ghost"
							disabled={busy === item.name}
							onclick={() => decide(item, null)}
						>
							Keuze wissen
						</Button>
					{/if}
				</Card.Footer>
			</Card.Root>
		{/each}
	</div>

	{#if loading}
		<p class="text-sm text-muted-foreground">Laden…</p>
	{:else if remaining > 0}
		<Button type="button" variant="outline" onclick={() => load(false)}>
			Meer laden ({remaining} over)
		</Button>
	{/if}
</section>
