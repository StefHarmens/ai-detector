<script lang="ts">
	import { onMount } from 'svelte';
	import RefreshCwIcon from '@lucide/svelte/icons/refresh-cw';
	import { Button } from '$lib/components/ui/button/index.js';
	import { version, type Versions } from '$lib/version';

	// Often enough to see an automatic update land within a few minutes.
	const REFRESH_MS = 60_000;

	let versions = $state<Versions | null>(null);
	let unreachable = $state(false);

	async function load() {
		try {
			const response = await fetch('/versie', { cache: 'no-store' });
			if (!response.ok) throw new Error(`HTTP ${response.status}`);
			versions = await response.json();
			unreachable = false;
		} catch {
			unreachable = true;
		}
	}

	onMount(() => {
		void load();
		const timer = setInterval(load, REFRESH_MS);
		const visible = () => {
			if (document.visibilityState === 'visible') void load();
		};
		document.addEventListener('visibilitychange', visible);
		return () => {
			clearInterval(timer);
			document.removeEventListener('visibilitychange', visible);
		};
	});

	const OK = 'bg-emerald-600';
	const DOWN = 'bg-red-500';
	const WAITING = 'bg-sidebar-foreground/30';

	const rows = $derived([
		{
			name: 'Detector',
			color: unreachable || versions?.detector === null ? DOWN : versions ? OK : WAITING,
			value: unreachable
				? '–'
				: versions === null
					? '…'
					: versions.detector === null
						? 'niet bereikbaar'
						: (versions.detector.version ?? 'onbekend')
		},
		{
			name: 'Web',
			color: unreachable ? DOWN : versions ? OK : WAITING,
			value: unreachable ? 'niet bereikbaar' : version
		}
	]);

	// The web interface was updated while this page was open.
	const newer = $derived(versions !== null && versions.web !== version ? versions.web : null);
</script>

<div class="grid gap-1.5 px-2 py-1.5 text-xs text-sidebar-foreground/70">
	{#each rows as row (row.name)}
		<div class="flex items-center gap-2">
			<span class={['size-2 shrink-0 rounded-full', row.color]} aria-hidden="true"></span>
			<span>{row.name}</span>
			<!-- Version numbers line up; words like "niet bereikbaar" read as text. -->
			<span class={['ms-auto truncate', /^v?\d/.test(row.value) && 'font-mono tabular-nums']}>
				{row.value}
			</span>
		</div>
	{/each}
	{#if newer}
		<Button size="sm" variant="secondary" class="mt-1 w-full" onclick={() => location.reload()}>
			<RefreshCwIcon />
			Web {newer} · vernieuwen
		</Button>
	{/if}
</div>
