<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ExternalLinkIcon from '@lucide/svelte/icons/external-link';
	import ImageOffIcon from '@lucide/svelte/icons/image-off';
	import BanIcon from '@lucide/svelte/icons/ban';
	import ScissorsIcon from '@lucide/svelte/icons/scissors';
	import { toast } from 'svelte-sonner';
	import VideoKnop from './video-knop.svelte';
	import {
		api,
		cowPhoto,
		formatDate,
		percent,
		sightingPhoto,
		type Sighting,
		type Slot
	} from './api';

	type Props = {
		sighting: Sighting;
		// The datalist with all cows, shared by the cards.
		cowList: string;
		onchange: (sighting: Sighting, wasOpen: boolean) => void;
	};

	let { sighting, cowList, onchange }: Props = $props();
	let values = $state<string[]>(['', '']);
	let busy = $state(false);
	let inputs = $state<(HTMLInputElement | null)[]>([null, null]);

	async function send(body: Record<string, unknown>, next?: number) {
		if (busy) return;
		busy = true;
		const wasOpen = sighting.open;
		try {
			const result = await api<{ message: string; item: Sighting }>(
				`sprongen/${sighting.id}`,
				'POST',
				body
			);
			toast.success(result.message);
			if (typeof body.slot === 'number') {
				values[body.slot] = '';
			}
			onchange(result.item, wasOpen);
			if (next !== undefined && result.item.slots[next].how === null) {
				inputs[next]?.focus();
			}
		} catch (error) {
			toast.error(error instanceof Error ? error.message : 'Opslaan lukte niet');
		} finally {
			busy = false;
		}
	}

	function setCow(slot: number, value: string) {
		if (!value.trim()) {
			inputs[slot]?.focus();
			return;
		}
		void send({ action: 'koe', slot, value }, 1 - slot);
	}

	function status(slot: Slot): { text: string; tone: 'done' | 'auto' | 'open' | 'bad' } {
		if (slot.how === 'boer' && slot.bad_photo) return { text: 'Foto klopt niet', tone: 'bad' };
		if (slot.how === 'boer') return { text: slot.label ?? 'Onbekend', tone: 'done' };
		if (slot.how === 'auto')
			return { text: `${slot.label} · herkend ${percent(slot.score)}`, tone: 'auto' };
		return { text: 'Nog invullen', tone: 'open' };
	}

	const toneClasses = {
		done: 'bg-emerald-600 text-white',
		auto: 'bg-sky-600 text-white',
		open: 'bg-amber-500 text-black',
		bad: ''
	} as const;
</script>

<Card.Root class={sighting.open ? '' : 'opacity-80'}>
	<Card.Header class="gap-1">
		<Card.Title class="flex flex-wrap items-center gap-2 text-base">
			<span>{formatDate(sighting.date)}</span>
			<span class="font-normal text-muted-foreground">· {sighting.camera}</span>
			{#if sighting.false}
				<Badge variant="destructive">Geen sprong</Badge>
			{:else if sighting.open}
				<Badge class="bg-amber-500 text-black">Open</Badge>
			{:else}
				<Badge class="bg-emerald-600 text-white">Klaar</Badge>
			{/if}
		</Card.Title>
		{#if sighting.false}
			<Card.Description>
				Geen sprong: telt niet mee, en gaat als fout voorbeeld naar het trainen.
			</Card.Description>
		{:else if sighting.split_wrong}
			<Card.Description>
				De splitsing klopt niet: deze foto's gaan niet in de koemappen. Vul de koeien in als je ze
				weet, dan telt de sprong wel mee.
			</Card.Description>
		{:else if !sighting.split}
			<Card.Description>
				De twee koeien waren niet los te zien: links wie sprong, rechts wie werd besprongen. Deze
				foto's gaan niet in de koemappen.
			</Card.Description>
		{:else if !sighting.role_certain}
			<Card.Description>
				Wie sprong is een gok. Klopt het niet, kies dan <b>Andersom</b>.
			</Card.Description>
		{/if}
	</Card.Header>
	<Card.Content class="grid gap-4 sm:grid-cols-2">
		{#each sighting.slots as slot (slot.slot)}
			{@const info = status(slot)}
			<div class="flex flex-col gap-2">
				<a
					href={sightingPhoto(sighting.id, slot.slot === 0 ? 'A' : 'B')}
					target="_blank"
					rel="noreferrer"
					class="block overflow-hidden rounded-md bg-black"
				>
					<img
						src={sightingPhoto(sighting.id, slot.slot === 0 ? 'A' : 'B')}
						alt="Koe {slot.name}"
						loading="lazy"
						class="aspect-[4/3] w-full object-contain"
					/>
				</a>
				<div class="flex flex-wrap items-center justify-between gap-2">
					<span class="text-sm font-semibold">{slot.title}</span>
					<Badge
						variant={info.tone === 'bad' ? 'destructive' : 'default'}
						class={toneClasses[info.tone]}
					>
						{info.text}
					</Badge>
				</div>

				{#if slot.candidates.length > 0 && !sighting.split_wrong}
					<div class="flex flex-wrap gap-2">
						{#each slot.candidates as candidate (candidate.cow)}
							{@const chosen = candidate.cow === slot.cow && slot.how === 'boer'}
							<Button
								type="button"
								size="sm"
								variant={chosen ? 'default' : 'outline'}
								disabled={busy}
								class="h-auto gap-2 py-1 ps-1"
								title="Kies {candidate.label}"
								onclick={() => setCow(slot.slot, candidate.cow)}
							>
								{#if candidate.photo}
									<img
										src={cowPhoto(candidate.cow, candidate.photo)}
										alt=""
										loading="lazy"
										class="size-10 rounded object-cover"
									/>
								{/if}
								<span>{candidate.label}</span>
								<span class="text-xs opacity-70">{percent(candidate.score)}</span>
							</Button>
						{/each}
					</div>
				{/if}

				<form
					class="flex gap-2"
					onsubmit={(event) => {
						event.preventDefault();
						setCow(slot.slot, values[slot.slot]);
					}}
				>
					<Input
						bind:ref={inputs[slot.slot]}
						bind:value={values[slot.slot]}
						list={cowList}
						placeholder="30, Anna of ?"
						title="Nummer of naam; ? = onbekend; nieuwe koe: 44 NL123456789"
						autocomplete="off"
						enterkeyhint="done"
						aria-label="Koe {slot.name}"
						disabled={busy}
					/>
					<Button type="submit" disabled={busy}>Opslaan</Button>
				</form>

				<div class="flex flex-wrap gap-2">
					{#if slot.how === 'auto' && slot.cow}
						<Button
							type="button"
							size="sm"
							variant="outline"
							disabled={busy}
							onclick={() => setCow(slot.slot, slot.cow ?? '')}
						>
							<CheckIcon /> Klopt
						</Button>
					{/if}
					<Button
						type="button"
						size="sm"
						variant="outline"
						disabled={busy}
						onclick={() => send({ action: 'onbekend', slot: slot.slot }, 1 - slot.slot)}
					>
						Onbekend
					</Button>
					{#if sighting.split && !sighting.split_wrong}
						<Button
							type="button"
							size="sm"
							variant="outline"
							disabled={busy}
							title="De foto laat niet één koe van deze sprong zien; hij gaat niet in een koemap"
							onclick={() => send({ action: 'fotofout', slot: slot.slot }, 1 - slot.slot)}
						>
							<ImageOffIcon /> Foto klopt niet
						</Button>
					{/if}
				</div>
			</div>
		{/each}
	</Card.Content>
	<Card.Footer class="flex flex-wrap gap-2">
		{#if sighting.video}
			<VideoKnop id={sighting.id} title="{formatDate(sighting.date)} · {sighting.camera}" />
		{/if}
		{#if sighting.split}
			<Button
				type="button"
				size="sm"
				variant="secondary"
				disabled={busy}
				onclick={() => send({ action: 'andersom' })}
			>
				<ArrowLeftRightIcon /> Andersom
			</Button>
		{/if}
		{#if sighting.split}
			<Button
				type="button"
				size="sm"
				variant="outline"
				disabled={busy}
				title="De twee foto's zijn niet de twee koeien van de sprong"
				onclick={() => send({ action: sighting.split_wrong ? 'splitgoed' : 'splitfout' })}
			>
				<ScissorsIcon />
				{sighting.split_wrong ? 'Splitsing klopt toch' : 'Splitsing klopt niet'}
			</Button>
		{/if}
		<Button
			type="button"
			size="sm"
			variant={sighting.false ? 'outline' : 'destructive'}
			disabled={busy}
			title="Zelfde als Fout onder de melding in Telegram"
			onclick={() => send({ action: sighting.false ? 'welsprong' : 'geensprong' })}
		>
			<BanIcon />
			{sighting.false ? 'Toch een sprong' : 'Geen sprong'}
		</Button>
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
	</Card.Footer>
</Card.Root>
