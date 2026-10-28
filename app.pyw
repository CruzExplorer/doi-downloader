"""DOI Downloader: the window. Our own design, tkinter only.

Runs under pythonw.exe (no console): nothing here reads the keyboard through
the console or prints to it. Errors go to %APPDATA%\\DOI Downloader\\error.log
and a message box.
This program never opens a browser or any other application.
"""
from __future__ import annotations

import io
import json
import queue
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pymupdf                                    # noqa: E402
import engine as E                                # noqa: E402
import importer as I                              # noqa: E402
import settings as ST                             # noqa: E402
import sources as S                               # noqa: E402

APP = 'DOI Downloader'
NOT_BOT = ('You are not a bot. Some publishers do not allow download programs, whoever starts them, '
           'so this program lists those papers instead of fetching them. It uses only free, legal copies '
           'and never pirate sites.')


# ------------------------------------------------------------------ error log (pythonw has no console)

def _log_error(kind, value, tb):
    try:
        p = ST.settings_dir() / 'error.log'
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'a', encoding='utf-8') as f:
            f.write(''.join(traceback.format_exception(kind, value, tb)) + '\n')
    except Exception:
        pass
    try:
        messagebox.showerror(APP, f'{kind.__name__}: {value}\n\nDetails are in {ST.settings_dir() / "error.log"}')
    except Exception:
        pass


sys.excepthook = _log_error
threading.excepthook = lambda a: _log_error(a.exc_type, a.exc_value, a.exc_traceback)


# ------------------------------------------------------------------ the window

class App(tk.Tk):
    def __init__(self, folder: Path = HERE, web_factory=None):
        super().__init__()
        self.title(APP)
        self.geometry('1180x760')
        self.folder = Path(folder)
        self.web_factory = web_factory                 # tests pass a fake network here
        self.cfg = ST.load()
        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.rows: list[dict] = []
        self.items: list[dict] = []
        self.engine: E.Engine | None = None
        self.worker: threading.Thread | None = None
        self._build()
        self.after(100, self._drain)

    # ---- layout
    def _build(self):
        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=True)
        self.nb = nb
        self.tab_download = ttk.Frame(nb)
        self.tab_results = ttk.Frame(nb)
        self.tab_missing = ttk.Frame(nb)
        self.tab_review = ttk.Frame(nb)
        self.tab_paper = ttk.Frame(nb)
        self.tab_reader = ttk.Frame(nb)
        self.tab_snow = ttk.Frame(nb)
        self.tab_search = ttk.Frame(nb)
        self.tab_setup = ttk.Frame(nb)
        for t, name in ((self.tab_download, 'Download'), (self.tab_results, 'Results'),
                        (self.tab_missing, 'Not downloadable automatically'), (self.tab_review, 'Review'),
                        (self.tab_paper, 'Paper page'), (self.tab_reader, 'PDF reader'),
                        (self.tab_snow, 'Citation snowballing'), (self.tab_search, 'Search'), (self.tab_setup, 'Setup')):
            nb.add(t, text=name)
        self._build_download()
        self._build_results()
        self._build_missing()
        self._build_review()
        self._build_paper()
        self._build_reader()
        self._build_snow()
        self._build_search()
        self._build_setup()
        self.status = tk.StringVar(value=NOT_BOT)
        ttk.Label(self, textvariable=self.status, wraplength=1150, anchor='w').pack(fill='x', padx=8, pady=4)

    def _build_download(self):
        f = self.tab_download
        ttk.Label(f, text='Paste DOIs, one per line (a column copied from Excel is fine):').pack(anchor='w', padx=8, pady=(8, 0))
        self.paste = tk.Text(f, height=18, wrap='none')
        self.paste.pack(fill='both', expand=True, padx=8, pady=4)
        bar = ttk.Frame(f)
        bar.pack(fill='x', padx=8, pady=4)
        self.btn_download = ttk.Button(bar, text='Download', command=self.start_download)
        self.btn_download.pack(side='left')
        ttk.Button(bar, text='Check availability (downloads nothing)', command=lambda: self.start_download(check_only=True)).pack(side='left', padx=4)
        ttk.Button(bar, text='Resume', command=self.start_download).pack(side='left', padx=4)
        self.btn_stop = ttk.Button(bar, text='Stop', command=self.stop_event.set, state='disabled')
        self.btn_stop.pack(side='left', padx=4)
        ttk.Button(bar, text='Import PDFs or ZIP…', command=self.import_files).pack(side='left', padx=12)
        ttk.Button(bar, text='Re-check filed PDFs', command=self.recheck).pack(side='left', padx=4)
        ttk.Button(bar, text='Export references', command=self.export_refs).pack(side='left', padx=4)
        ttk.Button(bar, text='Where are my PDFs?', command=self.show_folder).pack(side='left', padx=4)
        self.progress = tk.StringVar(value='')
        ttk.Label(f, textvariable=self.progress).pack(anchor='w', padx=8)

    def _build_results(self):
        f = self.tab_results
        cols = ('DOI', 'Status', 'Retracted', 'Proof', 'Version', 'Source', 'Round 1', 'Round 2', 'Round 3', 'File')
        self.tree = ttk.Treeview(f, columns=cols, show='headings', height=24)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=110 if c not in ('DOI', 'File', 'Source') else 220, anchor='w')
        self.tree.tag_configure('retracted', foreground='red')
        self.tree.tag_configure('ok', foreground='dark green')
        self.tree.tag_configure('check', foreground='dark orange')
        self.tree.pack(fill='both', expand=True, padx=8, pady=8)
        self.prisma_var = tk.StringVar(value='')
        ttk.Label(f, textvariable=self.prisma_var).pack(anchor='w', padx=8, pady=(0, 8))
        self.tree.bind('<Double-1>', lambda e: self.open_paper_page(self._selected_doi()))

    def _build_missing(self):
        f = self.tab_missing
        ttk.Label(f, text='These papers could not be fetched by a program. Import a copy you obtain another way, '
                          'or use the request text below.', wraplength=1100).pack(anchor='w', padx=8, pady=8)
        self.missing_list = tk.Listbox(f, height=10)
        self.missing_list.pack(fill='x', padx=8)
        self.missing_list.bind('<<ListboxSelect>>', lambda e: self._show_request())
        bar = ttk.Frame(f)
        bar.pack(fill='x', padx=8, pady=4)
        ttk.Button(bar, text='Author request', command=lambda: self._show_request('author')).pack(side='left')
        ttk.Button(bar, text='Library request', command=lambda: self._show_request('library')).pack(side='left', padx=4)
        ttk.Button(bar, text='ACM permission letter (all ACM DOIs)', command=self._show_acm_letter).pack(side='left', padx=4)
        ttk.Button(bar, text='Copy text', command=self._copy_request).pack(side='left', padx=12)
        self.request_text = tk.Text(f, height=16, wrap='word')
        self.request_text.pack(fill='both', expand=True, padx=8, pady=4)

    def _build_review(self):
        f = self.tab_review
        left = ttk.Frame(f)
        left.pack(side='left', fill='y', padx=8, pady=8)
        ttk.Label(left, text='Files in Check these:').pack(anchor='w')
        self.review_list = tk.Listbox(left, width=48, height=30)
        self.review_list.pack(fill='y', expand=True)
        self.review_list.bind('<<ListboxSelect>>', lambda e: self._review_show())
        ttk.Button(left, text='Refresh', command=self._review_refresh).pack(fill='x', pady=2)
        ttk.Button(left, text='This is correct → file it', command=self._review_accept).pack(fill='x', pady=2)
        ttk.Button(left, text='Wrong paper → keep it here', command=self._review_reject).pack(fill='x', pady=2)
        right = ttk.Frame(f)
        right.pack(side='left', fill='both', expand=True)
        self.review_facts = tk.Text(right, height=9, wrap='word')
        self.review_facts.pack(fill='x', padx=8, pady=(8, 4))
        self.review_canvas = tk.Label(right)
        self.review_canvas.pack(fill='both', expand=True)

    def _build_paper(self):
        f = self.tab_paper
        bar = ttk.Frame(f)
        bar.pack(fill='x', padx=8, pady=8)
        ttk.Label(bar, text='DOI:').pack(side='left')
        self.paper_doi = ttk.Entry(bar, width=50)
        self.paper_doi.pack(side='left', padx=4)
        ttk.Button(bar, text='Show', command=lambda: self.open_paper_page(self.paper_doi.get())).pack(side='left')
        self.paper_text = tk.Text(f, wrap='word')
        self.paper_text.pack(fill='both', expand=True, padx=8, pady=4)

    def _build_reader(self):
        f = self.tab_reader
        bar = ttk.Frame(f)
        bar.pack(fill='x', padx=8, pady=8)
        ttk.Button(bar, text='Open a filed PDF…', command=self._reader_open).pack(side='left')
        ttk.Button(bar, text='◀', command=lambda: self._reader_go(-1)).pack(side='left', padx=(12, 2))
        ttk.Button(bar, text='▶', command=lambda: self._reader_go(1)).pack(side='left')
        ttk.Button(bar, text='Zoom +', command=lambda: self._reader_zoom(1.25)).pack(side='left', padx=(12, 2))
        ttk.Button(bar, text='Zoom −', command=lambda: self._reader_zoom(0.8)).pack(side='left')
        self.reader_find = ttk.Entry(bar, width=24)
        self.reader_find.pack(side='left', padx=(12, 2))
        ttk.Button(bar, text='Find', command=self._reader_search).pack(side='left')
        self.reader_info = tk.StringVar(value='')
        ttk.Label(bar, textvariable=self.reader_info).pack(side='left', padx=12)
        self.reader_canvas = tk.Label(f)
        self.reader_canvas.pack(fill='both', expand=True)
        self.reader_doc, self.reader_page, self.reader_scale = None, 0, 1.0

    def _build_snow(self):
        f = self.tab_snow
        bar = ttk.Frame(f)
        bar.pack(fill='x', padx=8, pady=8)
        ttk.Label(bar, text='DOI:').pack(side='left')
        self.snow_doi = ttk.Entry(bar, width=50)
        self.snow_doi.pack(side='left', padx=4)
        ttk.Button(bar, text='References', command=lambda: self._snow('references')).pack(side='left')
        ttk.Button(bar, text='Citing papers', command=lambda: self._snow('citations')).pack(side='left', padx=4)
        ttk.Button(bar, text='Add selected to list', command=lambda: self._add_selected(self.snow_tree)).pack(side='left', padx=12)
        self.snow_tree = self._paper_tree(f)

    def _build_search(self):
        f = self.tab_search
        bar = ttk.Frame(f)
        bar.pack(fill='x', padx=8, pady=8)
        ttk.Label(bar, text='Keywords:').pack(side='left')
        self.search_q = ttk.Entry(bar, width=60)
        self.search_q.pack(side='left', padx=4)
        ttk.Button(bar, text='Search', command=self._search).pack(side='left')
        ttk.Button(bar, text='Add selected to list', command=lambda: self._add_selected(self.search_tree)).pack(side='left', padx=12)
        self.search_tree = self._paper_tree(f)

    def _paper_tree(self, parent):
        cols = ('DOI', 'Title', 'Year', 'Authors')
        t = ttk.Treeview(parent, columns=cols, show='headings', height=24, selectmode='extended')
        for c, w in zip(cols, (220, 520, 60, 300)):
            t.heading(c, text=c)
            t.column(c, width=w, anchor='w')
        t.pack(fill='both', expand=True, padx=8, pady=4)
        return t

    def _build_setup(self):
        f = self.tab_setup
        self.setup_vars = {}
        row = 0
        for key in ('email', *ST.KEY_NAMES):
            name, what, link = ST.GUIDE[key]
            ttk.Label(f, text=name, font=('Segoe UI', 10, 'bold')).grid(row=row, column=0, sticky='w', padx=8, pady=(10, 0))
            row += 1
            ttk.Label(f, text=what + f'  ({link})', wraplength=900, justify='left').grid(row=row, column=0, columnspan=3, sticky='w', padx=8)
            row += 1
            var = tk.StringVar(value=self.cfg['email'] if key == 'email' else self.cfg['keys'].get(key, ''))
            self.setup_vars[key] = var
            ttk.Entry(f, textvariable=var, width=60, show='' if key == 'email' else '•').grid(row=row, column=0, sticky='w', padx=8)
            if key in ('email', 'openalex', 'core'):
                ttk.Button(f, text='Test', command=lambda k=key: self._test_key(k)).grid(row=row, column=1, padx=4)
            row += 1
        ttk.Label(f, text='File-name template ({author}, {year}, {title}, {doi}):').grid(row=row, column=0, sticky='w', padx=8, pady=(10, 0))
        row += 1
        self.tmpl_var = tk.StringVar(value=self.cfg.get('template') or ST.DEFAULT_TEMPLATE)
        ttk.Entry(f, textvariable=self.tmpl_var, width=60).grid(row=row, column=0, sticky='w', padx=8)
        row += 1
        ttk.Button(f, text='Save', command=self._save_setup).grid(row=row, column=0, sticky='w', padx=8, pady=12)
        ttk.Label(f, text=f'Saved outside the program folder, in {ST.settings_path()}', wraplength=900).grid(row=row, column=1, columnspan=2, sticky='w')

    # ---- engine plumbing
    def _make_engine(self, stop=None):
        cfg = self.cfg
        web = self.web_factory(cfg['email']) if self.web_factory else S.Web(cfg['email'])
        return E.Engine(self.folder, cfg['email'], cfg.get('keys'), cfg.get('template'), cfg.get('resolvers'),
                        web=web, stop=stop or self.stop_event.is_set,
                        on_row=lambda r: self.q.put(('row', dict(r))), say=lambda m: self.q.put(('say', m)))

    def start_download(self, check_only=False):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP, 'A run is already going. Press Stop first.')
            return
        if not self.cfg['email'] and not check_only:
            messagebox.showinfo(APP, 'Please type your email in Setup first (Unpaywall needs it).')
            self.nb.select(self.tab_setup)
            return
        self.items = E.parse_input(self.paste.get('1.0', 'end'))
        if not any(it['doi'] for it in self.items):
            messagebox.showinfo(APP, 'No DOIs found in the box.')
            return
        self.rows = []
        self.tree.delete(*self.tree.get_children())
        self.stop_event.clear()
        self.btn_stop.configure(state='normal')
        self.btn_download.configure(state='disabled')
        self.engine = self._make_engine()
        unique = sum(1 for it in self.items if it['doi'] and it['dup_of'] is None)
        self.progress.set(f'{unique} DOIs. Free, legal copies only; no browser is opened.')
        self.nb.select(self.tab_results)

        def work():
            rows = self.engine.run(self.items, check_only=check_only)
            self.q.put(('done', rows, check_only))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == 'row':
                    self._add_row(msg[1])
                elif msg[0] == 'say':
                    self.progress.set(msg[1])
                elif msg[0] == 'done':
                    self._finish(msg[1], msg[2])
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _add_row(self, r):
        self.rows.append(r)
        tag = 'retracted' if r.get('Retracted') else ('ok' if r['Status'] in ('Verified', 'Already verified')
                                                      else 'check' if r['Status'] == 'Check these' else '')
        self.tree.insert('', 'end', values=[r.get(c, '') for c in self.tree['columns']], tags=(tag,))
        if r['Status'] == 'Not downloadable automatically':
            self.missing_list.insert('end', f"{r['DOI']}  —  {r.get('Title (registry)') or ''}")

    def _finish(self, rows, check_only):
        self.btn_stop.configure(state='disabled')
        self.btn_download.configure(state='normal')
        path = E.write_report(rows, self.folder / 'Report.csv')
        ok = E.totals_agree(self.items, rows)
        p = E.prisma(rows)
        self.prisma_var.set(f"PRISMA 2020: reports sought for retrieval {p['sought']} · retrieved {p['retrieved']} · "
                            f"not retrieved {p['not_retrieved']} {p['reasons'] if p['reasons'] else ''}")
        msg = f'Done. Report: {path}'
        if not ok:
            msg = 'THE TOTALS DO NOT ADD UP. Do not trust this report. ' + msg
        self.progress.set(msg)
        self._review_refresh()

    # ---- buttons
    def import_files(self):
        paths = filedialog.askopenfilenames(title='PDFs or ZIP files you obtained yourself',
                                            filetypes=[('PDF or ZIP', '*.pdf *.zip')])
        if not paths:
            return
        dois = [it['doi'] for it in E.parse_input(self.paste.get('1.0', 'end')) if it['doi']]
        if not dois:
            messagebox.showinfo(APP, 'Paste the DOIs first, so each file can be matched to one.')
            return
        eng = self._make_engine(stop=lambda: False)
        rows = I.import_files(eng, [Path(p) for p in paths], dois)
        lines = [f"{r['File given']}: {r['Status']} {r.get('Proof', '')} {r.get('Filed as', '')} {r.get('Note', '')}" for r in rows]
        messagebox.showinfo(APP, '\n'.join(lines) or 'No PDF found in the chosen files.')
        self._review_refresh()

    def recheck(self):
        eng = self.engine or self._make_engine(stop=lambda: False)
        out = eng.recheck()
        messagebox.showinfo(APP, f"Verified files present: {out['verified']}\nIn Check these: {out['check_these']}\n"
                                 f"Missing: {out['missing'] or 'none'}\nChanged since filing: {out['changed'] or 'none'}")

    def export_refs(self):
        eng = self.engine or self._make_engine(stop=lambda: False)
        ris, bib = E.export_references(eng.journal, self.folder)
        messagebox.showinfo(APP, f'Written:\n{ris}\n{bib}')

    def show_folder(self):
        self.clipboard_clear()
        self.clipboard_append(str(self.folder / 'PDFs'))
        messagebox.showinfo(APP, f'Your PDFs are in:\n{self.folder / "PDFs"}\n\n(The path is now on your clipboard.)')

    def _selected_doi(self):
        sel = self.tree.selection()
        return self.tree.item(sel[0])['values'][0] if sel else ''

    # ---- requests
    def _missing_row(self):
        sel = self.missing_list.curselection()
        if not sel:
            return None
        doi = self.missing_list.get(sel[0]).split('  —  ')[0]
        return next((r for r in self.rows if r['DOI'] == doi), None)

    def _show_request(self, kind='author'):
        r = self._missing_row()
        if not r:
            return
        self.request_text.delete('1.0', 'end')
        self.request_text.insert('1.0', E.request_texts(r)[kind])

    def _show_acm_letter(self):
        dois = sorted({r['DOI'] for r in self.rows if r['DOI'].startswith(S.NEVER_ASK_PREFIXES)
                       and r['Status'] not in ('Verified', 'Already verified')})
        self.request_text.delete('1.0', 'end')
        self.request_text.insert('1.0', E.acm_letter(dois))

    def _copy_request(self):
        self.clipboard_clear()
        self.clipboard_append(self.request_text.get('1.0', 'end'))

    # ---- review (own files only; no network)
    def _review_refresh(self):
        self.review_list.delete(0, 'end')
        d = self.folder / 'PDFs' / 'Check these'
        if d.is_dir():
            for p in sorted(d.glob('*.pdf')):
                self.review_list.insert('end', p.name)

    def _review_selected(self):
        sel = self.review_list.curselection()
        return (self.folder / 'PDFs' / 'Check these' / self.review_list.get(sel[0])) if sel else None

    def _review_show(self):
        p = self._review_selected()
        if not p:
            return
        journal = E.Engine(self.folder, '', web=S.Web('')).journal
        entry = next((j for j in journal.values() if (j.get('file') or '').endswith(p.name)), {})
        self.review_facts.delete('1.0', 'end')
        self.review_facts.insert('1.0', f"File: {p.name}\nRegistry title: {entry.get('title', '?')}\n"
                                        f"Authors: {', '.join(entry.get('authors') or [])}\nYear: {entry.get('year', '')}\n"
                                        f"Why it is here: {entry.get('reason', '')}")
        self.review_img = self._render(p, 0, 0.9)
        self.review_canvas.configure(image=self.review_img)

    def _review_accept(self):
        p = self._review_selected()
        if not p:
            return
        target = self.folder / 'PDFs' / p.name
        p.replace(target)
        self._note_decision(p.name, 'accepted by you on review')
        self._review_refresh()

    def _review_reject(self):
        p = self._review_selected()
        if p:
            self._note_decision(p.name, 'marked wrong by you on review')

    def _note_decision(self, name, text):
        eng = E.Engine(self.folder, '', web=S.Web(''))
        for doi, j in eng.journal.items():
            if (j.get('file') or '').endswith(name):
                j['review'] = text
                if 'accepted' in text:
                    j['file'], j['status'] = name, 'Verified'
                    eng._manifest(doi, name, 'review', '-', 'accepted on review', j.get('version', ''), j.get('sha256', ''))
        eng._save_journal()

    def _render(self, path, page_no, scale):
        with pymupdf.open(path) as doc:
            page_no = max(0, min(page_no, doc.page_count - 1))
            pix = doc[page_no].get_pixmap(matrix=pymupdf.Matrix(scale, scale))
            return tk.PhotoImage(data=pix.tobytes('png'))

    # ---- paper page
    def open_paper_page(self, doi):
        doi = (doi or '').strip()
        if not doi:
            return
        self.nb.select(self.tab_paper)
        self.paper_doi.delete(0, 'end')
        self.paper_doi.insert(0, doi)
        eng = self._make_engine(stop=lambda: False)
        st, f, note = eng.registry.facts(doi)
        self.paper_text.delete('1.0', 'end')
        if st != 'ok':
            self.paper_text.insert('1.0', f'{st}: {note}')
            return
        cands, notes = eng.finder.candidates(f)
        txt = (f"{f.title}\n\nAuthors: {', '.join(f.authors)}\nYear: {f.year}   Venue: {f.venue}\nDOI: {f.doi}\n"
               f"Retracted: {'YES' if f.retracted else 'no'}\n\nAbstract:\n{f.abstract or '(none in the registry)'}\n\n"
               f"Free copies a program may fetch: {len(cands)}\n" + '\n'.join(f'  - {c.label}: {c.url}' for c in cands)
               + ('\nNotes: ' + '; '.join(notes) if notes else ''))
        self.paper_text.insert('1.0', txt)

    # ---- reader (own files only)
    def _reader_open(self):
        p = filedialog.askopenfilename(initialdir=str(self.folder / 'PDFs'), filetypes=[('PDF', '*.pdf')])
        if not p:
            return
        self.reader_doc = Path(p)
        self.reader_page, self.reader_scale = 0, 1.0
        self._reader_draw()

    def _reader_draw(self):
        if not self.reader_doc:
            return
        self.reader_img = self._render(self.reader_doc, self.reader_page, self.reader_scale)
        self.reader_canvas.configure(image=self.reader_img)
        with pymupdf.open(self.reader_doc) as d:
            self.reader_info.set(f'{self.reader_doc.name}: page {self.reader_page + 1} of {d.page_count}')

    def _reader_go(self, delta):
        if self.reader_doc:
            self.reader_page = max(0, self.reader_page + delta)
            self._reader_draw()

    def _reader_zoom(self, factor):
        self.reader_scale *= factor
        self._reader_draw()

    def _reader_search(self):
        needle = self.reader_find.get().strip()
        if not (self.reader_doc and needle):
            return
        with pymupdf.open(self.reader_doc) as d:
            for i in range(d.page_count):
                if d[i].search_for(needle):
                    self.reader_page = i
                    self._reader_draw()
                    self.reader_info.set(self.reader_info.get() + f'   found "{needle}"')
                    return
        self.reader_info.set(f'"{needle}" not found')

    # ---- snowballing and search (metadata only)
    def _lookup(self):
        eng = self._make_engine(stop=lambda: False)
        return S.Lookup(eng.client, self.cfg['email'], self.cfg.get('keys'))

    def _fill(self, tree, papers):
        tree.delete(*tree.get_children())
        for p in papers:
            tree.insert('', 'end', values=(p['doi'], p['title'], p['year'], p['authors']))

    def _snow(self, kind):
        doi = self.snow_doi.get().strip()
        if doi:
            lk = self._lookup()
            self._fill(self.snow_tree, lk.references(doi) if kind == 'references' else lk.citations(doi))

    def _search(self):
        qtext = self.search_q.get().strip()
        if qtext:
            self._fill(self.search_tree, self._lookup().search(qtext))

    def _add_selected(self, tree):
        dois = [tree.item(i)['values'][0] for i in tree.selection()]
        dois = [d for d in dois if d]
        if dois:
            self.paste.insert('end', '\n' + '\n'.join(dois))
            self.nb.select(self.tab_download)

    # ---- setup
    def _save_setup(self):
        self.cfg['email'] = self.setup_vars['email'].get().strip()
        self.cfg['keys'] = {k: self.setup_vars[k].get().strip() for k in ST.KEY_NAMES if self.setup_vars[k].get().strip()}
        self.cfg['template'] = self.tmpl_var.get().strip() or ST.DEFAULT_TEMPLATE
        p = ST.save(self.cfg)
        messagebox.showinfo(APP, f'Saved to {p}')

    def _test_key(self, key):
        val = self.setup_vars[key].get().strip()
        web = self.web_factory(self.setup_vars['email'].get().strip()) if self.web_factory else S.Web(self.setup_vars['email'].get().strip())
        c = S.Client(web)
        if key == 'email':
            r = c.get(S.Registry.CROSSREF[:-1] + '?rows=1&mailto=' + val, accept='application/json')
        elif key == 'openalex':
            r = c.get('https://api.openalex.org/works?per_page=1', accept='application/json', headers={'Authorization': 'Bearer ' + val})
        else:
            r = c.get('https://api.core.ac.uk/v3/search/works?q=test&limit=1', accept='application/json', headers={'Authorization': 'Bearer ' + val})
        messagebox.showinfo(APP, f'{key}: HTTP {r.status} {r.reason}'.strip())


def main():
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
