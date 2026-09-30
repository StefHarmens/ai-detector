<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import * as Table from '$lib/components/ui/table';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { toast } from 'svelte-sonner';
	import { api, ApiError, cowPhoto, type Cow, type CowList } from './api';
	import KoeSprongen from './koe-sprongen.svelte';

	type Props = {
		list: CowList | null;
		error: string | null;
		archived: boolean;
		onreload: () => void;
		onarchived: (value: boolean) => void;
	};

	let { list, error, archived, onreload, onarchived }: Props = $props();

	let search = $state('');
	let selected = $state<Cow | null>(null);
	let photos = $state<string[]>([]);
	let photosLoading = $state(false);
	let busy = $state(false);

	let number = $state('');
	let lifeNumber = $state('');
	let name = $state('');
	// Set when the number belongs to another cow: then the farmer chooses.
	let conflict = $state<string | null>(null);

	const shown = $derived(
		(list?.items ?? []).filter((cow) => {
			const wanted = search.trim().toLowerCase();
			return (
				!wanted ||
				cow.label.toLowerCase().includes(wanted) ||
				cow.life_number.toLowerCase().includes(wanted) ||
				cow.work_number === wanted
			);
		})
	);

	async function open(cow: Cow) {
		selected = cow;
		photos = [];
		photosLoading = true;
		try {
			const result = await api<{ photos: string[] }>(
				`koeien/${encodeURIComponent(cow.life_number)}/fotos`
			);
			photos = result.photos;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			photosLoading = false;
		}
	}

	async function deletePhoto(photo: string) {
		if (
			!selected ||
			!confirm('Deze foto uit de map van de koe halen? De herkenning leert er dan niet meer van.')
		)
			return;
		try {
			await api(
				`koeien/${encodeURIComponent(selected.life_number)}/fotos/${encodeURIComponent(photo)}`,
				'DELETE'
			);
			photos = photos.filter((item) => item !== photo);
			toast.success('Foto verwijderd');
			onreload();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	async function archive(cow: Cow) {
		if (
			!confirm(
				`${cow.label} is van het bedrijf? Haar foto's gaan naar het archief; haar sprongen blijven bewaard.`
			)
		)
			return;
		try {
			const result = await api<{ message: string }>('koeien', 'POST', {
				action: 'weg',
				life_number: cow.life_number
			});
			toast.success(result.message);
			selected = null;
			onreload();
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		}
	}

	async function save(action: 'toevoegen' | 'wissel', oldLeft = false) {
		busy = true;
		try {
			const result = await api<{ message: string }>('koeien', 'POST', {
				action,
				number,
				life_number: lifeNumber,
				name,
				old_left: oldLeft
			});
			toast.success(result.message);
			number = lifeNumber = name = '';
			conflict = null;
			onreload();
		} catch (err) {
			if (err instanceof ApiError && err.status === 409) {
				conflict = err.message;
			} else {
				toast.error(err instanceof Error ? err.message : String(err));
			}
		} finally {
			busy = false;
		}
	}
</script>

<div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
	<div class="flex min-w-0 flex-col gap-3">
		<div class="flex flex-wrap items-center gap-2">
			<Input
				bind:value={search}
				placeholder="Zoek op nummer, naam, werknummer of levensnummer"
				class="max-w-sm"
			/>
			<Button
				type="button"
				size="sm"
				variant={archived ? 'default' : 'outline'}
				aria-pressed={archived}
				onclick={() => onarchived(!archived)}
			>
				Ook archief
			</Button>
		</div>
		{#if error}
			<p class="text-sm font-semibold text-destructive">{error}</p>
		{:else if list && list.items.length === 0}
			<p class="text-sm text-muted-foreground">
				Nog geen koeien. Zet de koeienlijst in de instellingen (herd_file), of voeg ze hier toe.
			</p>
		{:else if list}
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head class="w-12"></Table.Head>
						<Table.Head>Nummer</Table.Head>
						<Table.Head>Naam</Table.Head>
						<Table.Head>Werknr</Table.Head>
						<Table.Head class="hidden sm:table-cell">Levensnummer</Table.Head>
						<Table.Head class="text-end">Foto's</Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each shown as cow (cow.life_number)}
						<Table.Row class="cursor-pointer" onclick={() => open(cow)}>
							<Table.Cell class="py-1">
								{#if cow.photo}
									<img
										src={cowPhoto(cow.life_number, cow.photo)}
										alt=""
										loading="lazy"
										class="size-10 rounded object-cover"
									/>
								{/if}
							</Table.Cell>
							<Table.Cell class="font-medium">{cow.number ?? '–'}</Table.Cell>
							<Table.Cell>
								{cow.name ?? ''}
								{#if cow.archived}<Badge variant="secondary">weg sinds {cow.archived}</Badge>{/if}
							</Table.Cell>
							<Table.Cell>{cow.work_number ?? ''}</Table.Cell>
							<Table.Cell class="hidden font-mono text-xs sm:table-cell"
								>{cow.life_number}</Table.Cell
							>
							<Table.Cell class="text-end">
								{#if cow.photos === 0}
									<span class="text-muted-foreground">0</span>
								{:else}
									{cow.photos}
								{/if}
							</Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
			<p class="text-xs text-muted-foreground">
				Met 5 foto's en een duidelijke overeenkomst vult de herkenning een koe zelf in.
			</p>
		{/if}
	</div>

	<Card.Root class="h-fit min-w-0">
		<Card.Header>
			<Card.Title class="text-base">Koe toevoegen of nummer geven</Card.Title>
			<Card.Description>
				Het nummer is het halsbandnummer, of bij een pink zonder halsband het diernummer op haar
				oormerk.
				{#if list?.herd_file}
					De koeienlijst <span class="font-mono break-all">{list.herd_file}</span> is leidend: wat daar
					anders staat, wordt bij de volgende export weer overgenomen.
				{/if}
			</Card.Description>
		</Card.Header>
		<Card.Content>
			<form
				class="flex flex-col gap-3"
				onsubmit={(event) => {
					event.preventDefault();
					void save('toevoegen');
				}}
			>
				<div class="grid gap-1.5">
					<Label for="koe-nummer">Nummer</Label>
					<Input
						id="koe-nummer"
						bind:value={number}
						inputmode="numeric"
						required
						placeholder="30"
					/>
				</div>
				<div class="grid gap-1.5">
					<Label for="koe-levensnummer">Levensnummer</Label>
					<Input id="koe-levensnummer" bind:value={lifeNumber} required placeholder="NL123456789" />
				</div>
				<div class="grid gap-1.5">
					<Label for="koe-naam">Naam (mag leeg)</Label>
					<Input id="koe-naam" bind:value={name} placeholder="Bertha" />
				</div>
				{#if conflict}
					<div class="flex flex-col gap-2 rounded-md border p-3 text-sm">
						<p>{conflict}</p>
						<Button type="button" size="sm" disabled={busy} onclick={() => save('wissel', true)}>
							Nummer wisselen: oude koe is weg
						</Button>
						<Button
							type="button"
							size="sm"
							variant="outline"
							disabled={busy}
							onclick={() => save('wissel', false)}
						>
							Alleen halsband gewisseld
						</Button>
					</div>
				{:else}
					<Button type="submit" disabled={busy}>Opslaan</Button>
				{/if}
			</form>
		</Card.Content>
	</Card.Root>
</div>

<Dialog.Root open={selected !== null} onOpenChange={(value) => !value && (selected = null)}>
	<Dialog.Content class="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
		{#if selected}
			<Dialog.Header>
				<Dialog.Title>{selected.label}</Dialog.Title>
				<Dialog.Description>
					<span class="font-mono">{selected.life_number}</span>
					{#if selected.work_number}· werknummer {selected.work_number}{/if}
				</Dialog.Description>
			</Dialog.Header>
			{#if photosLoading}
				<p class="text-sm text-muted-foreground">Laden…</p>
			{:else if photos.length === 0}
				<p class="text-sm text-muted-foreground">
					Nog geen foto's. Die komen erbij zodra je haar bij een sprong invult.
				</p>
			{:else}
				<div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
					{#each photos as photo (photo)}
						<div class="group relative overflow-hidden rounded-md bg-black">
							<img
								src={cowPhoto(selected.life_number, photo)}
								alt={photo}
								loading="lazy"
								class="aspect-square w-full object-contain"
							/>
							<Button
								type="button"
								size="icon"
								variant="destructive"
								class="absolute end-1 top-1"
								title="Verkeerde koe: foto weghalen"
								onclick={() => deletePhoto(photo)}
							>
								<Trash2Icon />
							</Button>
						</div>
					{/each}
				</div>
				<p class="text-xs text-muted-foreground">
					Staat er een andere koe op een foto? Haal hem weg, anders leert de herkenning het
					verkeerde.
				</p>
			{/if}
			<h3 class="mt-2 text-sm font-semibold">Sprongen</h3>
			<KoeSprongen cow={selected.life_number} />
			{#if !selected.archived}
				<Dialog.Footer>
					<Button type="button" variant="outline" onclick={() => selected && archive(selected)}>
						Koe is van het bedrijf
					</Button>
				</Dialog.Footer>
			{/if}
		{/if}
	</Dialog.Content>
</Dialog.Root>
