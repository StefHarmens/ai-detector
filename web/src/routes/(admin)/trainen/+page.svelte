<script lang="ts">
	import * as Alert from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';

	let { data } = $props();
	const training = $derived(data.training);
	let copied = $state(false);
	let block = $state<HTMLPreElement | null>(null);

	const dateFormatter = new Intl.DateTimeFormat('nl-NL', {
		weekday: 'long',
		day: 'numeric',
		month: 'long',
		hour: '2-digit',
		minute: '2-digit'
	});

	async function copy() {
		try {
			await navigator.clipboard.writeText(training.command);
		} catch {
			// Over plain http on the network the browser has no clipboard API:
			// select the command, so Cmd+C copies it.
			const selection = window.getSelection();
			if (block && selection) {
				const range = document.createRange();
				range.selectNodeContents(block);
				selection.removeAllRanges();
				selection.addRange(range);
				document.execCommand('copy');
			}
		}
		copied = true;
		setTimeout(() => (copied = false), 2500);
	}
</script>

<svelte:head>
	<title>Trainen · CowCatcher</title>
</svelte:head>

<section class="max-w-4xl space-y-6">
	<header class="space-y-1">
		<h1 class="text-2xl font-semibold tracking-tight">Model trainen</h1>
		<p class="text-sm text-muted-foreground">
			Het model leert van de voorbeelden die je hebt goed- of afgekeurd: <b>Goed</b> en
			<b>Fout</b> onder de meldingen in Telegram, <b>Geen sprong</b> op de Koeien-pagina, en de
			keuzes bij <b>Twijfel</b>. Train opnieuw als er een flink aantal nieuwe voorbeelden is, zeker
			als het model steeds dezelfde fouten maakt.
		</p>
	</header>

	<div class="grid gap-3 sm:grid-cols-4">
		<Card.Root class="gap-1 py-4">
			<Card.Content class="px-4">
				<p class="text-xs text-muted-foreground">Goede voorbeelden</p>
				<p class="text-2xl font-semibold tabular-nums">{training.good}</p>
			</Card.Content>
		</Card.Root>
		<Card.Root class="gap-1 py-4">
			<Card.Content class="px-4">
				<p class="text-xs text-muted-foreground">Foute voorbeelden</p>
				<p class="text-2xl font-semibold tabular-nums">{training.bad}</p>
			</Card.Content>
		</Card.Root>
		<Card.Root class="gap-1 py-4 sm:col-span-2">
			<Card.Content class="px-4">
				<p class="text-xs text-muted-foreground">Laatste training</p>
				{#if training.trainedAt}
					<p class="text-base font-semibold">
						{dateFormatter.format(new Date(training.trainedAt))}
					</p>
					<p class="text-sm text-muted-foreground">
						{training.newSince === 0
							? 'Sindsdien geen nieuwe voorbeelden.'
							: `Sindsdien ${training.newSince} nieuwe ${training.newSince === 1 ? 'voorbeeld' : 'voorbeelden'}.`}
					</p>
				{:else}
					<p class="text-base font-semibold">Nog niet getraind</p>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	{#if training.problems.length > 0}
		<Alert.Root variant="destructive">
			<TriangleAlertIcon />
			<Alert.Title>Eerst nakijken</Alert.Title>
			<Alert.Description>
				<ul class="list-disc ps-4">
					{#each training.problems as problem (problem)}<li>{problem}</li>{/each}
				</ul>
			</Alert.Description>
		</Alert.Root>
	{/if}

	<div class="space-y-3">
		<h2 class="text-lg font-semibold">Zo train je</h2>
		<ol class="list-decimal space-y-2 ps-5 text-sm">
			<li>
				Open <b>Terminal</b> op de Mac mini (Cmd + spatie, typ <i>Terminal</i>, Enter).
			</li>
			<li>Kopieer het commando hieronder, plak het in Terminal en druk op Enter.</li>
			<li>
				De detector stopt; zolang er getraind wordt, komen er <b>geen meldingen</b>. Het trainen
				duurt al gauw een uur of langer. Laat het Terminal-venster open en zet de Mac mini niet uit.
			</li>
			<li>
				Daarna start de detector vanzelf weer, ook als het trainen mislukt. Gelukt? Dan staan er
				onderaan <i>Updated config.json</i> en <i>Saved trained model</i>, en gebruikt de detector
				het nieuwe model. De eerste start duurt een paar minuten, omdat het model wordt omgezet.
			</li>
		</ol>

		<div class="space-y-2">
			<div class="flex items-center justify-between gap-2">
				<span class="text-sm font-semibold">Commando</span>
				<Button type="button" size="sm" onclick={copy}>
					{#if copied}<CheckIcon /> Gekopieerd{:else}<CopyIcon /> Kopiëren{/if}
				</Button>
			</div>
			<pre
				bind:this={block}
				class="overflow-x-auto rounded-md bg-muted p-4 font-mono text-xs leading-relaxed">{training.command}</pre>
		</div>

		<p class="text-xs text-muted-foreground">
			Raakt het geheugen vol (de Mac mini wordt heel traag), stop dan met Ctrl + C en vervang
			<span class="font-mono">--batch 4</span> door <span class="font-mono">--batch 2</span>. Het
			trainen begint steeds opnieuw vanaf het basismodel
			{#if training.base}(<span class="font-mono">{training.base.split('/').at(-1)}</span>){/if},
			met alle voorbeelden; het oude model wordt overschreven, en
			<span class="font-mono">config.json.bak</span> bewaart de vorige instellingen.
		</p>
	</div>
</section>
