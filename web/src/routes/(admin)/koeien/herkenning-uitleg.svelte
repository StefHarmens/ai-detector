<script lang="ts">
	import * as Collapsible from '$lib/components/ui/collapsible';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import InfoIcon from '@lucide/svelte/icons/info';
	import { percent, type RecognitionRules } from './api';

	type Props = { rules: RecognitionRules | null };

	let { rules }: Props = $props();

	// Open until the farmer closes it; this browser remembers that.
	const KEY = 'koeien-uitleg-dicht';
	let open = $state(true);
	try {
		open = localStorage.getItem(KEY) !== '1';
	} catch {
		// No storage (private window): just open.
	}

	function toggle(value: boolean) {
		open = value;
		try {
			localStorage.setItem(KEY, value ? '0' : '1');
		} catch {
			// Not remembered, nothing else.
		}
	}

	const score = $derived(rules ? percent(rules.accept_score) : '90%');
	const margin = $derived(rules ? Math.round(rules.accept_margin * 100) : 8);
	const photos = $derived(rules?.min_photos ?? 5);
	// The colours of the badges on the cards.
	const legend = [
		['bg-sky-600', 'herkend door CowCatcher'],
		['bg-emerald-600', 'ingevuld door jou'],
		['bg-amber-500', 'nog invullen']
	] as const;
</script>

<Collapsible.Root {open} onOpenChange={toggle} class="rounded-lg border bg-muted/40">
	<Collapsible.Trigger
		class="flex w-full items-center gap-2 px-4 py-3 text-start text-sm font-semibold"
	>
		<InfoIcon class="size-4 shrink-0 text-sky-600" />
		Hoe herkent CowCatcher de koeien?
		<ChevronDownIcon class="ms-auto size-4 transition-transform {open ? 'rotate-180' : ''}" />
	</Collapsible.Trigger>
	<Collapsible.Content class="space-y-3 px-4 pb-4 text-sm">
		<ol class="list-decimal space-y-3 ps-5">
			<li>
				<b>De twee koeien los zien.</b> In de beelden van vlak voor de sprong zoekt CowCatcher de
				twee koeien elk apart. Lukt dat niet, omdat het druk is bij het voerhek, ze aan de rand van
				het beeld staan of de ene half achter de andere staat, dan herkent hij
				<b>geen van beide</b>. Je ziet dan een foto per rol en vult beide koeien zelf in.
			</li>
			<li>
				<b>Vergelijken met de koemappen.</b> Elke koe wordt apart vergeleken met de foto's in de
				koemappen. CowCatcher vult een koe alleen zelf in als:
				<ul class="mt-1 list-disc space-y-0.5 ps-5">
					<li>ze er minstens <b>{score}</b> op lijkt,</li>
					<li>ze minstens <b>{margin} punten</b> meer lijkt dan op de op één na beste koe, en</li>
					<li>haar map al minstens <b>{photos} foto's</b> heeft.</li>
				</ul>
				<p class="mt-1">
					Anders blijft ze open staan, met de koeien waar ze het meest op lijkt als knoppen, en
					staat er waarom ze niet herkend is. Het kan dus ook dat maar één van de twee koeien
					herkend is.
				</p>
			</li>
			<li>
				<b>Hoe meer je invult, hoe vaker hij zelf herkent.</b> Elke koe die jij invult, komt met
				haar foto in haar koemap. Klik bij een blauwe, zelf herkende koe op <b>Klopt</b>: pas dan
				gaat ook die foto in haar map. Foto's met <b>Foto klopt niet</b> of
				<b>Splitsing klopt niet</b>, en foto's waarop beide koeien staan, gaan nooit in een map.
			</li>
		</ol>
		<ul class="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
			{#each legend as [color, text] (text)}
				<li class="flex items-center gap-1.5">
					<span class="size-2.5 rounded-full {color}"></span>
					{text}
				</li>
			{/each}
		</ul>
	</Collapsible.Content>
</Collapsible.Root>
