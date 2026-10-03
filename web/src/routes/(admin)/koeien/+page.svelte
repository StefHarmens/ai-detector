<script lang="ts">
	import { untrack } from 'svelte';
	import { Badge } from '$lib/components/ui/badge';
	import * as Tabs from '$lib/components/ui/tabs';
	import { api, type CowList } from './api';
	import Beoordelen from './beoordelen.svelte';
	import KoeienLijst from './koeien-lijst.svelte';
	import Overzicht from './overzicht.svelte';

	let tab = $state('beoordelen');
	let open = $state<number | null>(null);
	let cows = $state<CowList | null>(null);
	let cowsError = $state<string | null>(null);
	let archived = $state(false);

	async function loadCows() {
		try {
			cows = await api<CowList>(`koeien${archived ? '?archief=1' : ''}`);
			cowsError = null;
		} catch (err) {
			cowsError = err instanceof Error ? err.message : String(err);
		}
	}

	$effect(() => {
		archived;
		// Again on every tab, since photo counts change while filling in mounts.
		tab;
		untrack(() => void loadCows());
	});
</script>

<svelte:head>
	<title>Koeien · CowCatcher</title>
</svelte:head>

<section class="space-y-6">
	<header class="space-y-1">
		<h1 class="text-2xl font-semibold tracking-tight">Koeien</h1>
		<p class="text-sm text-muted-foreground">
			Beoordeel per sprong of het er een was en welke koeien het zijn. In het overzicht zie je per
			koe hoe vaak ze besprongen werd, met de video's erbij.
		</p>
	</header>

	<Tabs.Root bind:value={tab} class="gap-4">
		<Tabs.List>
			<Tabs.Trigger value="beoordelen">
				Te beoordelen
				{#if open}<Badge class="ms-1 bg-amber-500 px-1.5 text-black">{open}</Badge>{/if}
			</Tabs.Trigger>
			<Tabs.Trigger value="koeien">Koeien</Tabs.Trigger>
			<Tabs.Trigger value="overzicht">Overzicht</Tabs.Trigger>
		</Tabs.List>
		<Tabs.Content value="beoordelen" class="flex flex-col gap-4">
			<Beoordelen
				cows={(cows?.items ?? []).filter((cow) => !cow.archived)}
				onopen={(count) => (open = count)}
			/>
		</Tabs.Content>
		<Tabs.Content value="koeien" class="flex flex-col gap-4">
			<KoeienLijst
				list={cows}
				error={cowsError}
				{archived}
				onreload={loadCows}
				onarchived={(value) => (archived = value)}
			/>
		</Tabs.Content>
		<Tabs.Content value="overzicht" class="flex flex-col gap-4">
			<!-- Loaded when opened, so it counts what was just filled in. -->
			{#if tab === 'overzicht'}<Overzicht />{/if}
		</Tabs.Content>
	</Tabs.Root>
</section>
