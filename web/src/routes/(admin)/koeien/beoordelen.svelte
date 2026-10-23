<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { NativeSelect, NativeSelectOption } from '$lib/components/ui/native-select';
	import { api, type Cow, type RecognitionRules, type Sighting, type SightingPage } from './api';
	import HerkenningUitleg from './herkenning-uitleg.svelte';
	import SprongCard from './sprong-card.svelte';

	const PAGE_SIZE = 10;

	type Props = {
		cows: Cow[];
		onopen: (count: number) => void;
	};

	let { cows, onopen }: Props = $props();

	let filter = $state<'open' | 'herkend' | 'alles'>('open');
	let camera = $state('');
	let items = $state<Sighting[]>([]);
	let total = $state(0);
	let cameras = $state<string[]>([]);
	let rules = $state<RecognitionRules | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let version = 0;

	const cowListId = 'koeien-lijst';

	async function load(reset: boolean) {
		const current = reset ? ++version : version;
		loading = true;
		error = null;
		try {
			const params = new URLSearchParams({
				filter,
				// Filled-in cards stay on screen but no longer count as open.
				offset: String(
					reset ? 0 : filter === 'open' ? items.filter((item) => item.open).length : items.length
				),
				limit: String(PAGE_SIZE)
			});
			if (camera) params.set('camera', camera);
			const page = await api<SightingPage>(`sprongen?${params}`);
			if (current !== version) return;
			const known = new Set(items.map((item) => item.id));
			items = reset ? page.items : [...items, ...page.items.filter((item) => !known.has(item.id))];
			total = page.total;
			cameras = page.cameras;
			rules = page.rules ?? null;
			openCount = page.open;
			onopen(openCount);
		} catch (err) {
			if (current === version) error = err instanceof Error ? err.message : String(err);
		} finally {
			if (current === version) loading = false;
		}
	}

	function changed(updated: Sighting, wasOpen: boolean) {
		// The card stays where it is until the next reload, so the farmer sees
		// what was saved.
		items = items.map((item) => (item.id === updated.id ? updated : item));
		if (wasOpen !== updated.open) {
			const step = updated.open ? 1 : -1;
			openCount = Math.max(0, openCount + step);
			if (filter === 'open') total = Math.max(0, total + step);
			onopen(openCount);
		}
	}

	let openCount = $state(0);
	const filterUitleg = {
		open: 'Sprongen waarbij nog minstens één koe ingevuld moet worden.',
		herkend:
			'Sprongen waarbij CowCatcher minstens één koe zelf herkende (blauw). Controleer ze en klik op Klopt; de andere koe kan nog open staan.',
		alles: 'Alle sprongen, ook de ingevulde en die zonder sprong.'
	} as const;
	const remaining = $derived(
		total - (filter === 'open' ? items.filter((item) => item.open).length : items.length)
	);
	$effect(() => {
		filter;
		camera;
		void load(true);
	});

	// Back from Telegram, where a mount may have been marked Fout or filled
	// in: show the list as it is now.
	function visible() {
		if (document.visibilityState === 'visible' && !loading) void load(true);
	}
</script>

<svelte:document onvisibilitychange={visible} />

<datalist id={cowListId}>
	{#each cows as cow (cow.life_number)}
		<option value={cow.number ?? cow.life_number}>{cow.label}</option>
	{/each}
</datalist>

<HerkenningUitleg {rules} />

<div class="flex flex-wrap items-center gap-2">
	{#each [['open', 'Nog invullen'], ['herkend', 'Zelf herkend'], ['alles', 'Alles']] as [value, label] (value)}
		<Button
			type="button"
			size="sm"
			variant={filter === value ? 'default' : 'outline'}
			aria-pressed={filter === value}
			onclick={() => (filter = value as typeof filter)}
		>
			{label}
		</Button>
	{/each}
	{#if cameras.length > 1}
		<NativeSelect bind:value={camera} aria-label="Camera" class="h-8">
			<NativeSelectOption value="">Alle camera's</NativeSelectOption>
			{#each cameras as name (name)}
				<NativeSelectOption value={name}>{name}</NativeSelectOption>
			{/each}
		</NativeSelect>
	{/if}
	<Button type="button" size="sm" variant="ghost" disabled={loading} onclick={() => load(true)}>
		Vernieuwen
	</Button>
</div>
<p class="-mt-2 text-sm text-muted-foreground">{filterUitleg[filter]}</p>

{#if error}
	<p class="text-sm font-semibold text-destructive">{error}</p>
{:else if items.length === 0 && !loading}
	<p class="text-sm text-muted-foreground">
		{filter === 'open'
			? 'Alles is ingevuld. Nieuwe sprongen verschijnen hier vanzelf na Vernieuwen.'
			: 'Nog geen sprongen.'}
	</p>
{/if}

<div class="grid gap-4 xl:grid-cols-2">
	{#each items as sighting (sighting.id)}
		<SprongCard {sighting} {rules} cowList={cowListId} onchange={changed} />
	{/each}
</div>

{#if loading}
	<p class="text-sm text-muted-foreground">Laden…</p>
{:else if items.length < total}
	<Button type="button" variant="outline" onclick={() => load(false)}>
		Meer laden ({total - items.length} over)
	</Button>
{/if}
