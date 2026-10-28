# DOI Downloader

Paste a list of DOIs. The program fetches each paper's free, legal copy from
services that permit programs, checks that every file really is that paper and
is complete, and files it. Every paper gets up to three rounds of download and
check before it is set aside for you to look at.

It runs on Windows with Python 3.11+ and [PyMuPDF](https://pymupdf.readthedocs.io).
It needs **no API keys and no payment**. Optional free keys add more sources.

## How to use it

1. Double-click **DOI Downloader** (the shortcut made by `make_shortcut.py`).
2. **Setup** tab, first time only: type your email. Unpaywall and Crossref ask
   programs to identify their user. Keys are optional.
3. **Download** tab: paste DOIs, one per line (a column copied from Excel is
   fine), then press **Download**.
4. **Results** tab: every paper ends as *Verified*, *Check these* (with the
   reason), *Not downloadable automatically*, or another plain status listed below.
5. For papers no program may fetch: use the request text in the
   **Not downloadable automatically** tab, or obtain the file another way
   (library, publisher bulk download, the author) and use **Import PDFs or ZIP**.
   Imported files get the same checks.

Your files are in `PDFs\`. Files that did not pass are in `PDFs\Check these\`,
and the **Review** tab shows each one's first page beside the registry's details
so you can accept or reject it.

## Where the files come from

| Source | Needs | Gives |
|---|---|---|
| Crossref, DataCite | nothing | the title, authors, pages and type used for checking; retraction notices |
| Unpaywall | your email | the free, legal copies of each DOI |
| Semantic Scholar, arXiv | nothing | preprints and author copies |
| OpenAlex | free key | OpenAlex's own PDF copies (about 100 a day on a free key; the program stops at 90) |
| CORE | free key | author copies in university repositories |
| Wiley, Elsevier, Springer Nature | free personal key each | the publisher's own PDFs through its research service |

The program identifies itself as `DOI-Downloader` with your email on every
request and keeps each service's pacing. It never contacts a publisher whose
rules do not allow download programs; those papers are listed for you instead.
It never opens a browser or any other application.

**You are not a bot.** Some publishers do not allow download programs, whoever
starts them. The program lists those papers instead of fetching them. It uses
only free, legal copies, never pirate sites.

## How a file is checked

The facts come from the DOI registry, never from the file.

- **Right paper:** the DOI printed on the first pages or in the PDF's metadata,
  together with the registry title in the file's title block (PASS-A); or the
  title plus the first author's surname, when no other DOI is printed and no
  same-title twin exists (PASS-B). Correction and retraction notices fail.
- **Complete:** the file opens, every page renders, it has at least two pages,
  and the page count fits the registry's page range when the registry gives one.
- **Three rounds:** a damaged file is fetched fresh once; a wrong file moves on
  to the next source. Files that fail all rounds go to `Check these` with one
  reason per round.

## Statuses

Verified · Already verified · Check these · Not downloadable automatically ·
No free legal copy · Not checkable: scanned · DOI not registered ·
Not checked (run again) · Duplicate · Not a DOI · Stopped

`Report.csv` has one row per pasted line, `Manifest.csv` records every filed
file with its sha256, and the Results tab shows the PRISMA 2020 counts
(*reports sought for retrieval* and *reports not retrieved*).

## Other features

- **Check availability:** looks up every DOI and downloads nothing.
- **Resume:** run again; verified papers are skipped.
- **Retraction warning:** retracted papers are flagged in red and in the file name.
- **Export references:** `References.ris` and `References.bib` of the verified papers.
- **Paper page, PDF reader, citation snowballing, search:** inside the window,
  using the same open services and your own files.

## Keys and privacy

Your email and keys are stored in `%APPDATA%\DOI Downloader\settings.json`,
outside the program folder, so they never travel with the code. Each key is
sent only to the service that issued it. Run `check_before_publish.py` before
sharing the folder; it refuses if it finds an email, a key, a settings file, a
PDF or a report.

## Licence

AGPL-3.0, because PyMuPDF, which does the PDF checking, is licensed under the
AGPL-3.0. See `LICENSE`.

## Tests

    python -m pytest tests

The tests use PDFs built in memory and a fake network. They never download a paper.
