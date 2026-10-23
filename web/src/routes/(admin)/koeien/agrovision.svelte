<script lang="ts">
	import { onMount } from 'svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Spinner } from '$lib/components/ui/spinner';
	import { toast } from 'svelte-sonner';
	import { api, type Agrovision } from './api';

	let { onchanged }: { onchanged: () => void } = $props();

	let link = $state<Agrovision | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);

	let url = $state('');
	let username = $state('');
	let password = $state('');
	let path = $state('');
	let runAt = $state('03:30');
	let editing = $state(false);

	const running = $derived(link?.status === 'running');

	function take(next: Agrovision) {
		const wasRunning = link?.status === 'running';
		link = next;
		if (!editing) {
			url = next.url;
			username = next.username;
			runAt = next.run_at;
			path = next.path;
		}
		// The cows follow the list that was just fetched.
		if (wasRunning && next.status !== 'running') onchanged();
	}

	async function load() {
		try {
			take(await api<Agrovision>('agrovision'));
			error = null;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	async function run<T>(action: () => Promise<T>, done?: string) {
		busy = true;
		try {
			await action();
			if (done) toast.success(done);
		} catch (err) {
			toast.error(err instanceof Error ? err.message : String(err));
		} finally {
			busy = false;
		}
	}

	const save = () =>
		run(async () => {
			take(
				await api<Agrovision>('agrovision', 'POST', {
					url,
					username,
					password,
					run_at: runAt,
					path
				})
			);
			password = '';
			editing = false;
		}, 'Opgeslagen, de lijst wordt nu opgehaald');

	const syncNow = () => run(async () => take(await api<Agrovision>('agrovision/sync', 'POST')));

	const disconnect = () => {
		if (!confirm('De koppeling met AgroVision verwijderen, met het opgeslagen wachtwoord?')) return;
		return run(async () => {
			take(await api<Agrovision>('agrovision', 'DELETE'));
			url = username = password = '';
			editing = false;
		}, 'Koppeling verwijderd');
	};

	function when(value: string | null) {
		return value
			? new Date(value).toLocaleString('nl-NL', { dateStyle: 'medium', timeStyle: 'short' })
			: '–';
	}

	// Quickly while a run is going, else now and then.
	$effect(() => {
		const timer = setInterval(() => void load(), running ? 2000 : 15000);
		return () => clearInterval(timer);
	});

	onMount(() => void load());
</script>

<Card.Root>
	<Card.Header>
		<Card.Title class="flex items-center gap-2">
			AgroVision
			{#if running}
				<Badge variant="secondary"><Spinner class="me-1 size-3" /> Bezig</Badge>
			{:else if link?.status === 'ok'}
				<Badge class="bg-green-600 text-white">Gekoppeld</Badge>
			{:else if link?.status === 'failed'}
				<Badge variant="destructive">Mislukt</Badge>
			{:else if link?.configured}
				<Badge variant="secondary">Nog niet opgehaald</Badge>
			{:else}
				<Badge variant="outline">Niet gekoppeld</Badge>
			{/if}
		</Card.Title>
		<Card.Description>
			Vul de inloggegevens van AgroVision één keer in. De koeienlijst (Dieranalyse, exporteren naar
			Excel) wordt daarna elke nacht automatisch opgehaald, zodat je de koeien alleen in AgroVision
			of CRV bijhoudt. Dieren die daar weg zijn, worden hier gearchiveerd.
		</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4">
		{#if error}
			<p class="text-sm text-destructive">{error}</p>
		{/if}

		{#if link?.status === 'failed'}
			<div class="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm">
				<p class="font-medium">De laatste poging is mislukt</p>
				<p>{link.reason}</p>
				<p class="mt-1 text-muted-foreground">
					Geprobeerd op {when(link.attempted)}. De oude lijst blijft gebruikt tot het weer lukt.
				</p>
			</div>
		{:else if running && link?.step}
			<p class="text-sm text-muted-foreground">{link.step}…</p>
		{/if}

		{#if link?.configured}
			<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
				<dt class="text-muted-foreground">Laatst gelukt</dt>
				<dd>
					{when(link.succeeded)}{#if link.animals}
						· {link.animals} dieren{/if}
				</dd>
				<dt class="text-muted-foreground">Laatste poging</dt>
				<dd>{when(link.attempted)}</dd>
				<dt class="text-muted-foreground">Elke nacht om</dt>
				<dd>{link.run_at}</dd>
				{#if link.path}
					<dt class="text-muted-foreground">Rapport</dt>
					<dd>{link.path}</dd>
				{/if}
				<dt class="text-muted-foreground">Gebruiker</dt>
				<dd>{link.username}</dd>
			</dl>
		{/if}

		{#if !link?.configured || editing}
			<form
				class="grid max-w-xl gap-3"
				onsubmit={(event) => {
					event.preventDefault();
					void save();
				}}
			>
				<div class="grid gap-1.5">
					<Label for="agro-url">Adres van het rapport</Label>
					<Input
						id="agro-url"
						bind:value={url}
						placeholder="https://… (de pagina met de koeienlijst)"
						autocomplete="off"
					/>
				</div>
				<div class="grid gap-1.5">
					<Label for="agro-path">Weg naar het rapport</Label>
					<Input
						id="agro-path"
						bind:value={path}
						placeholder="Dieranalyse > Koeienlijst"
						autocomplete="off"
					/>
					<p class="text-xs text-muted-foreground">
						Wat je in AgroVision aanklikt, in volgorde en gescheiden door &gt;, tot de knop
						'exporteren naar Excel' in beeld is. Laat leeg als het adres al het rapport is.
					</p>
				</div>
				<div class="grid gap-1.5">
					<Label for="agro-user">Gebruikersnaam</Label>
					<Input id="agro-user" bind:value={username} autocomplete="off" />
				</div>
				<div class="grid gap-1.5">
					<Label for="agro-pass">Wachtwoord</Label>
					<Input
						id="agro-pass"
						type="password"
						bind:value={password}
						placeholder={link?.has_password ? 'Ongewijzigd laten' : ''}
						autocomplete="new-password"
					/>
				</div>
				<div class="grid gap-1.5">
					<Label for="agro-time">Ophalen om</Label>
					<Input id="agro-time" type="time" bind:value={runAt} class="w-32" />
				</div>
				<div class="flex gap-2">
					<Button type="submit" disabled={busy || running}>Opslaan en testen</Button>
					{#if editing}
						<Button type="button" variant="outline" onclick={() => (editing = false)}>
							Annuleren
						</Button>
					{/if}
				</div>
				<p class="text-xs text-muted-foreground">
					Het wachtwoord staat alleen op de computer van de detector en komt niet meer terug in deze
					pagina. De eerste keer wordt een browser geïnstalleerd; dat duurt enkele minuten.
				</p>
			</form>
		{:else}
			<div class="flex flex-wrap gap-2">
				<Button onclick={syncNow} disabled={busy || running}>Nu ophalen</Button>
				<Button variant="outline" onclick={() => (editing = true)} disabled={running}>
					Gegevens wijzigen
				</Button>
				<Button variant="outline" onclick={disconnect} disabled={busy || running}>
					Loskoppelen
				</Button>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
