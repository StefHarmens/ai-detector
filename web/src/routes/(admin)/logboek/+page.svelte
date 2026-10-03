<script lang="ts">
	import { onMount } from 'svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import CircleXIcon from '@lucide/svelte/icons/circle-x';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import PowerIcon from '@lucide/svelte/icons/power';
	import type { LogEntry, LogPage } from '$lib/server/logs';

	type Filter = 'problemen' | 'fouten' | 'updates' | 'alles';

	const REFRESH_MS = 30_000;
	const filters: { value: Filter; label: string; matches: (entry: LogEntry) => boolean }[] = [
		{ value: 'problemen', label: 'Problemen', matches: (entry) => entry.level !== 'info' },
		{ value: 'fouten', label: 'Fouten', matches: (entry) => entry.level === 'error' },
		{ value: 'updates', label: 'Updates', matches: (entry) => entry.source === 'updater' },
		{ value: 'alles', label: 'Alles', matches: () => true }
	];

	let filter = $state<Filter>('problemen');
	let page = $state<LogPage | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let loadedAt = $state<Date | null>(null);

	async function load() {
		loading = true;
		try {
			const response = await fetch('/logboek/api', { cache: 'no-store' });
			if (!response.ok) throw new Error(`Het logboek lezen lukte niet (HTTP ${response.status})`);
			page = await response.json();
			loadedAt = new Date();
			error = null;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
		const timer = setInterval(() => {
			if (document.visibilityState === 'visible') void load();
		}, REFRESH_MS);
		return () => clearInterval(timer);
	});

	const entries = $derived(page?.entries ?? []);
	const counts = $derived(
		Object.fromEntries(
			filters.map((option) => [option.value, entries.filter(option.matches).length])
		) as Record<Filter, number>
	);
	const visible = $derived(
		entries.filter(filters.find((option) => option.value === filter)!.matches)
	);

	// Entries are newest first; a heading per day.
	const days = $derived.by(() => {
		const groups: { day: string; entries: LogEntry[] }[] = [];
		for (const entry of visible) {
			const day = entry.time.slice(0, 10);
			if (groups.at(-1)?.day !== day) groups.push({ day, entries: [] });
			groups.at(-1)!.entries.push(entry);
		}
		return groups;
	});

	const dayFormatter = new Intl.DateTimeFormat('nl-NL', {
		weekday: 'long',
		day: 'numeric',
		month: 'long'
	});

	function dayLabel(day: string): string {
		const today = new Date();
		const local = (date: Date) =>
			`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
		if (day === local(today)) return 'Vandaag';
		const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
		if (day === local(yesterday)) return 'Gisteren';
		const date = new Date(`${day}T12:00:00`);
		return Number.isNaN(date.getTime()) ? day : dayFormatter.format(date);
	}

	// "aidetector.sources.hires" → "sources.hires"
	function shortLogger(logger: string | null): string | null {
		return logger?.replace(/^aidetector\./, '') ?? null;
	}
</script>

<svelte:head>
	<title>Logboek · CowCatcher</title>
</svelte:head>

<section class="space-y-6">
	<header class="space-y-1">
		<h1 class="text-2xl font-semibold tracking-tight">Logboek</h1>
		<p class="max-w-3xl text-sm text-muted-foreground">
			Waarschuwingen en fouten van de detector, en wat de updater heeft geïnstalleerd. Nieuwste
			bovenaan; de pagina vernieuwt zichzelf elke halve minuut. Een losse waarschuwing is meestal
			niet erg: de detector herstelt zelf. Komt dezelfde fout steeds terug, dan is er iets mis.
		</p>
	</header>

	<div class="flex flex-wrap items-center gap-2">
		{#each filters as option (option.value)}
			<Button
				type="button"
				size="sm"
				variant={filter === option.value ? 'default' : 'outline'}
				aria-pressed={filter === option.value}
				onclick={() => (filter = option.value)}
			>
				{option.label}{#if page}&nbsp;({counts[option.value]}){/if}
			</Button>
		{/each}
		<Button type="button" size="sm" variant="ghost" disabled={loading} onclick={load}>
			Vernieuwen
		</Button>
		{#if loadedAt}
			<span class="text-xs text-muted-foreground">
				bijgewerkt {loadedAt.toLocaleTimeString('nl-NL')}
			</span>
		{/if}
	</div>

	{#if error}
		<p class="text-sm font-semibold text-destructive">{error}</p>
	{:else if page && page.directory === null}
		<Card.Root>
			<Card.Content class="space-y-2 text-sm text-muted-foreground">
				<p>
					Er zijn geen logbestanden. Die schrijft CowCatcher als hij met het installatiescript als
					achtergrondservice draait, in <span class="font-mono">~/Library/Logs/CowCatcher</span>.
				</p>
				<p>
					Start je de <span class="font-mono">.command</span>-bestanden met de hand, dan staat de
					log alleen in het Terminal-venster.
				</p>
			</Card.Content>
		</Card.Root>
	{:else if page && visible.length === 0}
		<p class="text-sm text-muted-foreground">
			{filter === 'updates' ? 'Nog geen updates in het logboek.' : 'Geen waarschuwingen of fouten.'}
		</p>
	{/if}

	{#each days as group (group.day)}
		<div class="space-y-2">
			<h2 class="text-sm font-medium text-muted-foreground">{dayLabel(group.day)}</h2>
			<Card.Root class="gap-0 py-0">
				<ul class="divide-y">
					{#each group.entries as entry, index (entry.time + entry.source + index)}
						<li class="flex gap-3 px-4 py-3">
							<span class="mt-0.5 shrink-0" aria-hidden="true">
								{#if entry.level === 'error'}
									<CircleXIcon class="size-4 text-destructive" />
								{:else if entry.level === 'warning'}
									<TriangleAlertIcon class="size-4 text-amber-500" />
								{:else if entry.source === 'updater'}
									<DownloadIcon class="size-4 text-emerald-600" />
								{:else}
									<PowerIcon class="size-4 text-muted-foreground" />
								{/if}
							</span>
							<div class="min-w-0 flex-1 space-y-1">
								<div
									class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"
								>
									<span class="font-mono tabular-nums">{entry.time.slice(11)}</span>
									<Badge variant="outline" class="font-normal">
										{entry.source === 'detector' ? 'Detector' : 'Updater'}
									</Badge>
									{#if entry.level === 'error'}
										<Badge variant="destructive">Fout</Badge>
									{:else if entry.level === 'warning'}
										<Badge class="bg-amber-500 text-white">Waarschuwing</Badge>
									{/if}
									{#if shortLogger(entry.logger)}
										<span class="font-mono">{shortLogger(entry.logger)}</span>
									{/if}
								</div>
								<p class="text-sm break-words">{entry.message}</p>
								{#if entry.details}
									<details class="text-xs">
										<summary class="cursor-pointer text-muted-foreground select-none">
											Details
										</summary>
										<!-- Zero width plus full minimum: a long line scrolls in here
										instead of widening the page. -->
										<pre
											class="mt-2 max-h-80 w-0 min-w-full overflow-auto rounded-md bg-muted p-3 font-mono whitespace-pre">{entry.details}</pre>
									</details>
								{/if}
							</div>
						</li>
					{/each}
				</ul>
			</Card.Root>
		</div>
	{/each}
</section>
