<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import * as Table from '$lib/components/ui/table';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { api, type Overview } from './api';
	import KoeSprongen from './koe-sprongen.svelte';

	let days = $state(7);
	let overview = $state<Overview | null>(null);
	let error = $state<string | null>(null);
	// The cow whose mounts are shown under her row.
	let expanded = $state<string | null>(null);

	const most = $derived(Math.max(1, ...(overview?.items ?? []).map((item) => item.mounted)));

	async function load() {
		error = null;
		try {
			overview = await api<Overview>(`overzicht?dagen=${days}`);
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	$effect(() => {
		days;
		void load();
	});
</script>

<div class="flex flex-wrap items-center gap-2">
	{#each [1, 3, 7, 14, 30] as value (value)}
		<Button
			type="button"
			size="sm"
			variant={days === value ? 'default' : 'outline'}
			aria-pressed={days === value}
			onclick={() => (days = value)}
		>
			{value === 1 ? 'Laatste 24 uur' : `${value} dagen`}
		</Button>
	{/each}
</div>

{#if error}
	<p class="text-sm font-semibold text-destructive">{error}</p>
{:else if overview}
	<p class="text-sm text-muted-foreground">
		{overview.mounts}
		{overview.mounts === 1 ? 'sprong' : 'sprongen'}
		{#if overview.unknown > 0}
			· {overview.unknown} {overview.unknown === 1 ? 'koe' : 'koeien'} niet ingevuld
		{/if}
		· 🔥 = besprongen, mogelijk tochtig
	</p>
	{#if overview.items.length === 0}
		<p class="text-sm text-muted-foreground">Nog geen herkende koeien in deze periode.</p>
	{:else}
		<p class="text-xs text-muted-foreground">
			Klik op een koe voor haar sprongen, met foto's en video.
		</p>
		<Table.Root class="max-w-4xl">
			<Table.Header>
				<Table.Row>
					<Table.Head>Koe</Table.Head>
					<Table.Head>Werknr</Table.Head>
					<Table.Head>Besprongen</Table.Head>
					<Table.Head class="text-end">Zelf gesprongen</Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{#each overview.items as item (item.cow)}
					<Table.Row
						class="cursor-pointer"
						aria-expanded={expanded === item.cow}
						onclick={() => (expanded = expanded === item.cow ? null : item.cow)}
					>
						<Table.Cell class="font-medium">
							<span class="inline-flex items-center gap-1">
								{#if expanded === item.cow}<ChevronDownIcon
										class="size-4"
									/>{:else}<ChevronRightIcon class="size-4" />{/if}
								{item.mounted > 0 ? '🔥 ' : ''}{item.label}
							</span>
						</Table.Cell>
						<Table.Cell>{item.work_number ?? ''}</Table.Cell>
						<Table.Cell>
							<div class="flex items-center gap-2">
								<div
									class="h-2 rounded-full bg-orange-500"
									style="width: {(item.mounted / most) * 8}rem"
								></div>
								<span>{item.mounted}×</span>
							</div>
						</Table.Cell>
						<Table.Cell class="text-end">{item.mounting}×</Table.Cell>
					</Table.Row>
					{#if expanded === item.cow}
						<Table.Row class="hover:bg-transparent">
							<Table.Cell colspan={4} class="bg-muted/30 whitespace-normal">
								<KoeSprongen cow={item.cow} {days} />
							</Table.Cell>
						</Table.Row>
					{/if}
				{/each}
			</Table.Body>
		</Table.Root>
	{/if}
{/if}
