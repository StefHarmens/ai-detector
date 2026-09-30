<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import ExternalLinkIcon from '@lucide/svelte/icons/external-link';
	import { api, formatDate, sightingPhoto, type Sighting, type SightingPage } from './api';
	import VideoKnop from './video-knop.svelte';

	type Props = {
		cow: string;
		// Only the last days; all mounts when not given.
		days?: number;
	};

	let { cow, days }: Props = $props();
	let items = $state<Sighting[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load(more = false) {
		loading = true;
		error = null;
		try {
			const params = new URLSearchParams({
				filter: 'alles',
				koe: cow,
				offset: String(more ? items.length : 0),
				limit: '20'
			});
			if (days) params.set('dagen', String(days));
			const page = await api<SightingPage>(`sprongen?${params}`);
			items = more ? [...items, ...page.items] : page.items;
			total = page.total;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		cow;
		days;
		void load();
	});

	/** Her role in the mount and who the other cow was. */
	function describe(sighting: Sighting): { role: string; mounted: boolean; other: string } {
		const mine = sighting.slots.find((slot) => slot.cow === cow) ?? sighting.slots[0];
		const other = sighting.slots.find((slot) => slot !== mine);
		return {
			role: mine.mounter ? 'Sprong' : 'Werd besprongen',
			mounted: !mine.mounter,
			other: other?.label ?? 'onbekend'
		};
	}
</script>

{#if error}
	<p class="text-sm font-semibold text-destructive">{error}</p>
{:else if !loading && items.length === 0}
	<p class="text-sm text-muted-foreground">Geen sprongen in deze periode.</p>
{/if}

<ul class="flex flex-col divide-y">
	{#each items as sighting (sighting.id)}
		{@const info = describe(sighting)}
		<li class="flex flex-wrap items-center gap-3 py-2">
			<div class="flex gap-1">
				{#each ['A', 'B'] as name (name)}
					{#if sighting.photos.includes(name)}
						<a href={sightingPhoto(sighting.id, name)} target="_blank" rel="noreferrer">
							<img
								src={sightingPhoto(sighting.id, name)}
								alt="Koe {name}"
								loading="lazy"
								class="size-14 rounded bg-black object-cover"
							/>
						</a>
					{/if}
				{/each}
			</div>
			<div class="min-w-40 flex-1 text-sm">
				<div class="font-medium">{formatDate(sighting.date)}</div>
				<div class="text-muted-foreground">{sighting.camera}</div>
			</div>
			<div class="flex min-w-40 flex-1 flex-wrap items-center gap-2 text-sm">
				<Badge
					class={info.mounted ? 'bg-orange-500 text-white' : ''}
					variant={info.mounted ? 'default' : 'secondary'}
				>
					{info.mounted ? '🔥 ' : ''}{info.role}
				</Badge>
				<span class="text-muted-foreground">
					{info.mounted ? 'door' : 'op'}
					{info.other}
				</span>
			</div>
			<div class="flex gap-2">
				{#if sighting.video}
					<VideoKnop id={sighting.id} title="{formatDate(sighting.date)} · {sighting.camera}" />
				{/if}
				{#if sighting.photos.includes('controle')}
					<Button
						href={sightingPhoto(sighting.id, 'controle')}
						target="_blank"
						rel="noreferrer"
						size="sm"
						variant="ghost"
					>
						<ExternalLinkIcon /> Hele beeld
					</Button>
				{/if}
			</div>
		</li>
	{/each}
</ul>

{#if loading}
	<p class="text-sm text-muted-foreground">Laden…</p>
{:else if items.length < total}
	<Button type="button" size="sm" variant="outline" onclick={() => load(true)}>
		Meer ({total - items.length})
	</Button>
{/if}
