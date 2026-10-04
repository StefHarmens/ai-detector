<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { NativeSelect, NativeSelectOption } from '$lib/components/ui/native-select';
	import CameraIcon from '@lucide/svelte/icons/camera';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ImagePlusIcon from '@lucide/svelte/icons/image-plus';
	import { toast } from 'svelte-sonner';
	import { api, foundCowPhoto, foundPhoto, type Camera, type FoundCows } from './api';

	type Props = {
		// Life number and name of the cow the photos are for.
		cow: string;
		label: string;
		onadded: (photo: string, count: number) => void;
	};

	let { cow, label, onadded }: Props = $props();

	let cameras = $state<Camera[]>([]);
	let camera = $state('0');
	let found = $state<FoundCows | null>(null);
	let added = $state<number[]>([]);
	// The cow under the mouse, marked in the photo and in the row below it.
	let hovered = $state<number | null>(null);
	// The cow clicked, added only once the farmer confirms: a wrong one in
	// her folder teaches the recognition the wrong cow.
	let picked = $state<number | null>(null);
	let busy = $state<string | null>(null);
	let input = $state<HTMLInputElement | null>(null);

	$effect(() => {
		api<Camera[]>('cameras')
			.then((list) => (cameras = list))
			.catch(() => (cameras = []));
	});

	async function search(path: string, body: Blob | undefined, message: string) {
		busy = message;
		found = null;
		added = [];
		picked = null;
		try {
			found = await api<FoundCows>(path, 'POST', body);
		} catch (error) {
			toast.error(error instanceof Error ? error.message : 'Zoeken lukte niet');
		} finally {
			busy = null;
		}
	}

	function chosen(event: Event) {
		const file = (event.currentTarget as HTMLInputElement).files?.[0];
		if (file) void search('koeien/zoek', file, 'Koeien zoeken…');
		// The same file can be chosen again.
		(event.currentTarget as HTMLInputElement).value = '';
	}

	function fromCamera() {
		void search(`koeien/zoek?camera=${camera}`, undefined, 'Foto maken en koeien zoeken…');
	}

	function pick(index: number) {
		if (busy || added.includes(index)) return;
		picked = picked === index ? null : index;
	}

	async function add() {
		const index = picked;
		if (!found || busy || index === null || added.includes(index)) return;
		busy = 'Toevoegen…';
		try {
			const result = await api<{ message: string; photo: string; photos: number }>(
				`koeien/${encodeURIComponent(cow)}/fotos`,
				'POST',
				{ token: found.token, index }
			);
			added = [...added, index];
			picked = null;
			toast.success(result.message);
			onadded(result.photo, result.photos);
		} catch (error) {
			toast.error(error instanceof Error ? error.message : 'Toevoegen lukte niet');
		} finally {
			busy = null;
		}
	}
</script>

<div class="space-y-3 rounded-lg border p-3">
	<div class="space-y-1">
		<h3 class="text-sm font-semibold">Foto toevoegen</h3>
		<p class="text-xs text-muted-foreground">
			Zo herkent CowCatcher {label} al voordat ze bij sprongen is ingevuld. Het beste werkt een foto
			<b>uit een stalcamera</b>, van bovenaf: daar vergelijkt de herkenning mee. Kies een moment
			waarop ze los en helemaal in beeld staat. Een foto van opzij met je telefoon helpt minder.
		</p>
	</div>

	<div class="flex flex-wrap items-center gap-2">
		{#if cameras.length > 0}
			<NativeSelect bind:value={camera} aria-label="Camera" class="h-9" disabled={busy !== null}>
				{#each cameras as item (item.index)}
					<NativeSelectOption value={String(item.index)}>{item.name}</NativeSelectOption>
				{/each}
			</NativeSelect>
			<Button type="button" disabled={busy !== null} onclick={fromCamera}>
				<CameraIcon /> Uit camera
			</Button>
		{/if}
		<Button type="button" variant="outline" disabled={busy !== null} onclick={() => input?.click()}>
			<ImagePlusIcon /> Foto kiezen
		</Button>
		<input bind:this={input} type="file" accept="image/*" class="hidden" onchange={chosen} />
		{#if busy}<span class="text-sm text-muted-foreground">{busy}</span>{/if}
	</div>

	{#if found}
		{#if found.cows.length === 0}
			<p class="text-sm text-muted-foreground">
				Geen koe gevonden op deze foto. Kies een foto waarop ze helemaal te zien is.
			</p>
		{:else}
			<p class="text-sm">
				Klik op <b>{label}</b>, in de foto of in de rij eronder, en bevestig met
				<b>Toevoegen</b>. Staan er meer koeien van haar op andere foto's, voeg die dan ook toe: hoe
				meer, hoe beter.
			</p>
			<div class="relative overflow-hidden rounded-md bg-black">
				<img
					src={foundPhoto(found.token)}
					alt="Gevonden koeien"
					class="block h-auto w-full"
					style="aspect-ratio: {found.width} / {found.height}"
				/>
				{#each found.cows as item (item.index)}
					{@const [x1, y1, x2, y2] = item.box}
					{@const done = added.includes(item.index)}
					<button
						type="button"
						disabled={busy !== null || done}
						title={done ? 'Toegevoegd' : `Koe ${item.index + 1} kiezen`}
						aria-pressed={picked === item.index}
						class="absolute rounded-sm transition-colors {done
							? 'border-2 border-emerald-500 bg-emerald-500/20'
							: picked === item.index
								? 'z-10 border-4 border-sky-500 bg-sky-500/30'
								: hovered === item.index
									? 'border-2 border-sky-300 bg-sky-300/20'
									: 'border-2 border-amber-400'}"
						onmouseenter={() => (hovered = item.index)}
						onmouseleave={() => (hovered = null)}
						style="left: {x1 * 100}%; top: {y1 * 100}%; width: {(x2 - x1) * 100}%; height: {(y2 -
							y1) *
							100}%"
						onclick={() => pick(item.index)}
					>
						<span
							class="absolute start-0 top-0 flex items-center gap-1 rounded-br px-1.5 text-xs font-semibold {done
								? 'bg-emerald-500 text-white'
								: picked === item.index
									? 'bg-sky-500 text-white'
									: 'bg-amber-400 text-black'}"
						>
							{#if done}<CheckIcon class="size-3" /> Toegevoegd{:else}{item.index + 1}{/if}
						</span>
					</button>
				{/each}
			</div>
			<div class="grid grid-cols-3 gap-2 sm:grid-cols-5">
				{#each found.cows as item (item.index)}
					{@const done = added.includes(item.index)}
					<button
						type="button"
						aria-label="Koe {item.index + 1}"
						title={done ? 'Toegevoegd' : `Koe ${item.index + 1} kiezen`}
						disabled={busy !== null || done}
						aria-pressed={picked === item.index}
						class="relative overflow-hidden rounded-md bg-black {done
							? 'border-2 border-emerald-500'
							: picked === item.index
								? 'border-4 border-sky-500'
								: hovered === item.index
									? 'border-2 border-sky-300'
									: 'border-2 border-transparent'}"
						onmouseenter={() => (hovered = item.index)}
						onmouseleave={() => (hovered = null)}
						onclick={() => pick(item.index)}
					>
						<img
							src={foundCowPhoto(found.token, item.index)}
							alt=""
							loading="lazy"
							class="aspect-square w-full object-contain"
						/>
						<span
							class="absolute start-0 top-0 flex items-center gap-1 rounded-br px-1.5 text-xs font-semibold {done
								? 'bg-emerald-500 text-white'
								: picked === item.index
									? 'bg-sky-500 text-white'
									: 'bg-amber-400 text-black'}"
						>
							{#if done}<CheckIcon class="size-3" /> Toegevoegd{:else}{item.index + 1}{/if}
						</span>
					</button>
				{/each}
			</div>
			{#if picked !== null}
				<!-- Stays in view while scrolling through the photo and the row. -->
				<div
					class="sticky bottom-0 flex flex-wrap items-center gap-3 rounded-md border bg-background p-2 shadow-md"
				>
					<img
						src={foundCowPhoto(found.token, picked)}
						alt="Gekozen koe"
						class="size-14 rounded bg-black object-contain"
					/>
					<p class="text-sm">Is koe {picked + 1} echt <b>{label}</b>?</p>
					<div class="ms-auto flex flex-wrap gap-2">
						<Button type="button" variant="outline" onclick={() => (picked = null)}>
							Andere koe
						</Button>
						<Button type="button" disabled={busy !== null} onclick={add}>
							<CheckIcon /> Toevoegen aan {label}
						</Button>
					</div>
				</div>
			{/if}
		{/if}
	{/if}
</div>
