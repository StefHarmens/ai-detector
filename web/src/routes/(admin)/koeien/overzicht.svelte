<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import * as Table from '$lib/components/ui/table';
	import { api, type Overview } from './api';

	let days = $state(7);
	let overview = $state<Overview | null>(null);
	let error = $state<string | null>(null);

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
		<Table.Root class="max-w-2xl">
			<Table.Header>
				<Table.Row>
					<Table.Head>Koe</Table.Head>
					<Table.Head>Besprongen</Table.Head>
					<Table.Head class="text-end">Zelf gesprongen</Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{#each overview.items as item (item.cow)}
					<Table.Row>
						<Table.Cell class="font-medium">
							{item.mounted > 0 ? '🔥 ' : ''}{item.label}
						</Table.Cell>
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
				{/each}
			</Table.Body>
		</Table.Root>
	{/if}
{/if}
