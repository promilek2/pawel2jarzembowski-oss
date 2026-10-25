# Termin

**Find the earliest publicly funded (NFZ) specialist appointment in Poland, and see where it is,
how far away, how to get there and which number to call.**

Termin searches the public NFZ waiting-list data. It understands everyday Polish (and typos:
“okulsta”, “rezonans kolana”, “zacma”), computes distances from you and suggests what to say on
the phone. It can also notify you when an earlier slot appears. No ads, and an account is optional.
Open source. **Not an official NFZ application.**

[Wersja polska](README.md)

![Search results on a phone](docs/img/telefon-wyniki.png)

<sub>Screenshots show fictional test data.</sub>

## Who is it for

- Anyone with a referral (*skierowanie*) who wants a reasonable appointment soon.
- Seniors: a large-text mode, simple forms, a “Call” button and a phone script.
- Adult children helping their parents: share a link by text message and add a “call tomorrow
  morning” reminder to the calendar.
- Journalists and researchers: waiting-list history, medians and percentiles per province,
  and CSV/JSON export.

## Features

- Understands what you are looking for: synonyms, typos, missing Polish diacritics. Asks
  whether you are searching for a child or an adult.
- Distance from an address, postcode or GPS. Optional driving time (OSRM).
- Sorting modes: earliest, nearest, or balanced (“how many extra km am I willing to travel
  for each day sooner”). Each result explains in one sentence why it is listed where it is.
- Data-quality warnings: data older than 14 days, dates in the past, missing phone numbers.
  The “as of” date is always shown.
- Notifications about earlier slots via ntfy (no account), Telegram, e-mail or desktop.
- Optional account: recovery codes (password reset works even without e-mail), two-step
  verification with an authenticator app, list of signed-in devices, data export and deletion.
- Signed-in extras: “I checked by phone” reports (aggregated: does the NFZ date match what the
  clinic says?), “My cases” with referrals, visit dates and private notes, and Web Push
  notifications on your phone with a reminder the day before a visit.
- Trends: whether a facility's queue is getting shorter, provincial medians, a district heat map.
- Mobile-first PWA that works offline, WCAG 2.1 AA, in Polish, English and Ukrainian.

## Quick start

```bash
git clone https://github.com/promilek2/pawel2jarzembowski-oss.git termin && cd termin
docker compose up -d        # http://localhost:8000
```

or

```bash
pipx install termin-nfz
termin szukaj okulista --gdzie "81-310 Gdynia" --promien 30
```

CLI commands are in Polish: `szukaj` (search), `statystyki` (stats), `historia` (history),
`eksport` (export), `obserwuj` (watch), `serwer` (web server). See `termin --help`.
Deployment: [docs/DEPLOY.md](docs/DEPLOY.md) (in Polish).

## For journalists and researchers

Every search and watcher run stores a snapshot of public NFZ data, so the instance gradually
builds a history that the official search does not show. Export it with
`termin eksport --format csv` or `/api/export.csv`. When publishing, credit the source as
**NFZ “Terminy leczenia”** (public data).

## FAQ

**Is this official?** No. It is an independent open-source project that uses the public NFZ API,
the same one behind [terminyleczenia.nfz.gov.pl](https://terminyleczenia.nfz.gov.pl).

**Where does the data come from?** The NFZ “Terminy leczenia” API. Facilities report their
dates to NFZ. Geocoding uses OpenStreetMap Nominatim. See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

**Why doesn't the date match when I call?** Facilities update their data roughly once a week.
Always confirm by phone. We show the “as of” date and warn about stale data.

**Is this medical advice?** No. We only show facts about appointment dates.

**Privacy?** No analytics or ads; the account is optional. See [PRIVACY.md](PRIVACY.md).

## License

[MIT](LICENSE).
