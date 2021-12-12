__author__ = "Shane Drabing"
__license__ = "MIT"
__email__ = "shane.drabing@gmail.com"


# IMPORTS


import csv
import functools
import json
import math
import os
import random
import re
import statistics
import sys
import time

import bs4
import requests


# CONSTANTS


PATTERN_WORD = re.compile(r"[\w.]+")

FORM_NCBI = "https://{}.ncbi.nlm.nih.gov/{}".format
FORM_EUTILS = FORM_NCBI("eutils", "entrez/eutils/{}.cgi").format

TUP_LINKNAME = ("pubmed_pubmed_citedin", "pubmed_pubmed_five")

FORM_NODE = '{} [label="{}" href="{}" tooltip="{}" fillcolor="{}" margin={}]'.format
FORM_EDGE = "{}:n->{}:s [penwidth={}]".format
FORM_GRAPH = """
digraph {{

pad=0.7
layout=dot
rankdir=BT
ranksep=0.5
nodesep=0.0
splines=true
outputorder=edgesfirst

node [shape=note style=filled fontsize=9 fillcolor=none target="_blank" ordering="in"]
edge [arrowhead=none]

{}

{}

}}
""".lstrip().format


# ENUMS


class State:
    FILEIO, MAIN, EXIT = range(3)


# FUNCTIONS (GENERAL)


def adjust(text, target=":", shift=2):
    pad = " " * (shift + text.find(target))
    return text.replace("\n", "\n" + pad)


def wrap(text, length=80):
    words = text.replace("-", "- ").split()
    if len(words) < 2:
        return text

    lines = list()
    line, *words = words
    for x in words:
        trial = " ".join((line, x))
        if len(trial) < length:
            line = trial
        else:
            lines.append(line.replace("- ", "-"))
            line = x
    if line:
        lines.append(line.replace("- ", "-"))

    return "\n".join(lines)


def tokenize(x):
    if x is None:
        return list()
    return PATTERN_WORD.findall(x)


def jaccard(x, y, use_counts=True):
    wx = tuple(map(str.upper, tokenize(x)))
    wy = tuple(map(str.upper, tokenize(y)))
    tx = table(wx)
    ty = table(wy)

    union = set(tx) | set(ty)
    intersection = set(tx) & set(ty)
    if not use_counts:
        return len(intersection) / len(union)

    total = sum((*tx.values(), *ty.values()))
    shared = sum(tx[k] + ty[k] for k in intersection)
    if total == 0:
        return 0

    return shared / total


# FUNCTIONS (MATH)


def table(itr):
    itr = tuple(itr)
    lst = sorted(set(itr))
    return dict(zip(lst, map(itr.count, lst)))


def scale(itr):
    mu = statistics.mean(itr)
    sd = statistics.stdev(itr)
    return tuple((x - mu) / sd for x in itr)


def minmax(itr):
    low = min(itr)
    rng = max(itr) - low
    return tuple((x - low) / rng for x in itr)


def to_base(n, base):
    if n < base:
        return [n]
    return to_base(n // base, base) + [n % base]


def from_base(lst, base):
    return sum(n * (base ** i) for i, n in enumerate(lst[::-1]))


def hex(n):
    if not 0 <= n < 256:
        raise ValueError("number out of range")
    lst = ([0] + to_base(n, 16))[-2:]
    return "".join("0123456789ABCDEF"[i] for i in lst)


def lerp(x, y, i):
    return type(x)(x + (y - x) * i)


def lerp_vec(x, y, i):
    return tuple(lerp(xx, yy, i) for xx, yy in zip(x, y))


# FUNCTIONS (TIME)


def rate_limit(key, interval, lookup=dict()):
    if key not in lookup:
        lookup[key] = 0
    while (time.monotonic() - lookup[key]) < interval:
        time.sleep(0.01)
    lookup[key] = time.monotonic()


# FUNCTIONS (XML)


def soup(resp):
    if (resp.status_code != 200):
        return None
    return bs4.BeautifulSoup(resp.content, "lxml")


def text(node, sep=str(), strip=True):
    if node is None:
        return
    text = node.get_text(sep)
    if text is None:
        text = str()
    if bool(strip):
        return text.strip()
    return text


def select_text(node, selector, sep=str(), strip=True, many=False):
    if bool(many):
        return tuple(text(x, sep, strip) for x in node.select(selector))
    return text(node.select_one(selector), sep, strip)


# FUNCTIONS (EUTILS)


def eutils(cgi, tries=3, **options):
    rate_limit("eutils", 0.35)
    url = FORM_EUTILS(cgi)
    resp = None
    while (resp is None) or (tries <= 0) and (resp.status_code != 200):
        resp = requests.post(url, data=options)
        tries -= 1
    return resp


def esearch_pubmed(term):
    return eutils("esearch", db="pubmed", sort="relevance", term=term)


def esearch_pubmed_pmids(term):
    resp = esearch_pubmed(term)
    xml = soup(resp)
    return select_text(xml, "id", many=True)


def efetch_pubmed(pmids):
    str_pmids = ",".join(map(str, pmids))
    return eutils("efetch", db="pubmed", retmode="xml", id=str_pmids)


def efetch_pubmed_articles(pmids):
    resp = efetch_pubmed(pmids)
    xml = soup(resp)
    articles = xml.select("pubmedarticle")

    lst = list()
    for article in articles:
        journal = article.select_one("journal")
        authors = article.select("author")
    
        dct = {
            "pmid": select_text(article, "pmid"),
            "title": select_text(article, "articletitle"),
            "authors": tuple(map(author_full, authors)),
            "journal": select_text(journal, "title"),
            "date": select_text(journal, "pubdate", sep=" "),
            "abstract": select_text(article, "abstracttext"),
            "references": select_text(article, "reference articleid", many=1)
        }

        lst.append(dct)
    return lst


def elink_pubmed(pmid):
    return eutils(
        "elink", dbfrom="pubmed", db="pubmed", cmd="neighbor_score", id=pmid
    )


# FUNCTIONS (PUBMED)


def orcid_id(node):
    text = select_text(node, "identifier[source='ORCID']")
    if text is None:
        return 
    return text.split("/")[-1]


def author_text(node):
    return "{}, {} {}".format(
        select_text(node, "lastname"),
        select_text(node, "forename"),
        select_text(node, "initials")
    )


def author_full(node):
    return (orcid_id(node), author_text(node))


def article_summary(dct):
    lnames = ", ".join(x[-1].split(",")[0] for x in dct["authors"])
    return "\n".join((
        " PMID: " + dct["pmid"],
        adjust("Title: " + wrap(dct["title"], 69)),
        adjust("   By: " + wrap(lnames, 69)),
    ))


def article_summary_wide(dct):
    lnames = ", ".join(x[-1].split(",")[0] for x in dct["authors"])
    return "\n".join((
        "Title: " + dct["title"],
        "~",
        "By: " + lnames,
        "Date: " + dct["date"],
        "~",
        "Abstract: " + str(dct["abstract"]).replace('"', "'"),
        "~",
        "PMID: " + dct["pmid"],
        "Journal: " + dct["journal"]
    ))


def article_reference(dct):
    authors = tuple(x[-1].split(",")[0] for x in dct["authors"])
    n = len(authors)
    if n == 2:
        authors_str = " & ".join(authors)
    elif n <= 3:
        authors_str = ", & ".join(authors).replace(", &", ", ", n - 2)
    else:
        authors_str = authors[0] + ", et al."
    date_str = dct["date"].split()[0]
    return wrap("{} ({})".format(authors_str, date_str), 20)


def article_link(dct):
    resp = elink_pubmed(dct["pmid"])

    # make sure all keys are initialized
    for name in TUP_LINKNAME:
        key = name.split("_")[-1]
        dct[key] = list()

    # then fill the actual content
    xml_score = soup(resp)
    linksets = xml_score.select("linksetdb")
    for linkset in linksets:
        name = select_text(linkset, "linkname")
        if name not in TUP_LINKNAME:
            continue
        key = name.split("_")[-1]
        value = select_text(linkset, "id", many=True)
        dct[key] = value

    return dct


# FUNCTIONS (REPL)


def printt(*args, shift=4, **kwargs):
    pad = " " * shift
    args = (
        pad + str(x).replace("\n", "\n" + pad)
        for x in args
    )
    print(*args, **kwargs)


def printe(*args, **kwargs):
    printt(*args, file=sys.stderr, **kwargs)


def tuplefy(dct):
    for k, v in dct.items():
        if isinstance(v, list):
            dct[k] = tuple(v)
        elif isinstance(v, dict):
            dct[k] = tuplefy(v)
    return dct


def repl_par():
    return {
        "state": State.FILEIO,
        "data": None,
        "fpath": None,
    }


def repl_load(par):
    with open(par["fpath"] + ".json", "r", encoding="utf8") as fh:
        par["data"] = tuplefy(json.load(fh))


def repl_save(par):
    with open(par["fpath"] + ".json", "w", encoding="utf8") as fh:
        json.dump(par["data"], fh)


def repl_search(term):
    pmids = esearch_pubmed_pmids(term)
    lst = efetch_pubmed_articles(pmids)
    if not lst:
        printe("No results!")
    else:
        printt("\n\n".join(map(article_summary, lst)))


def repl_add(par, pmids):
    lst = efetch_pubmed_articles(pmids)
    for dct in map(article_link, lst):
        printt("Found {}...".format(dct["pmid"]))
        par["data"][dct["pmid"]] = dct


def repl_grow(par):
    counts = dict()
    def anon(x):
        return counts.get(x), random.random()

    for dct in par["data"].values():
        for src in ("five", "references", "citedin"):
            for key in dct[src]:
                try:
                    counts[key] += 1
                except KeyError:
                    counts[key] = 1

    keys = set(counts) - set(par["data"])
    pmids = tuple(sorted(keys, key=anon, reverse=True)[:5])
    repl_add(par, pmids)


def repl_graph(par, args, echo=True):
    dct = par["data"]

    if echo is True:
        printt(wrap(" ".join(dct.keys()), 76))

    edges = set()
    for _ in range(3):
        for k, v in dct.items():
            if "references" in v:
                for x in v["references"]:
                    if x in dct and x != k:
                        edges.add((x, k))
            if "citedin" in v:
                for x in v["citedin"]:
                    if x in dct and x != k:
                        edges.add((k, x))
    inbound, outbound = zip(*edges)

    nodes = set()
    for x in map(set, edges):
        nodes |= x
    nodes = tuple(map(dct.get, sorted(nodes)))

    nodes_lst = list()
    for x in nodes:
        pmid = x["pmid"]
        label = article_reference(x)
        href = "https://pubmed.ncbi.nlm.nih.gov/{}/".format(x["pmid"])
        tooltip = article_summary_wide(x).replace('"', "'")
        i1 = inbound.count(pmid)
        i2 = outbound.count(pmid)
        rgb = lerp_vec([255, 220, 140], [150, 230, 255], i1 / (i1 + i2))
        color = "#" + "".join(map(hex, rgb))
        size = 0.05 + math.log10(1 + len(x["citedin"])) / 10
        node_str = FORM_NODE(pmid, label, href, tooltip, color, size)
        nodes_lst.append(node_str)
    nodes_str = "\n    ".join(nodes_lst)

    edges_lst = list()
    for x, y in sorted(edges):
        size = 50 * jaccard(dct[x]["abstract"], dct[y]["abstract"]) ** 6
        edge_str = FORM_EDGE(x, y, f"{size:.6f}")
        edges_lst.append(edge_str)
    edges_str = "\n    ".join(edges_lst)

    dir_out = "gref/gv/"
    if not os.path.exists(dir_out):
        os.makedirs(dir_out)

    graph = FORM_GRAPH(nodes_str, edges_str)
    fpath = dir_out + par["fpath"].split("/")[-1] + ".gv"
    print(graph, file=open(fpath, "w", encoding="utf8"))

    return fpath


def repl_render(par, args, cmd):
    cmd = cmd.lower()

    dir_out = f"gref/{cmd}/"
    if not os.path.exists(dir_out):
        os.makedirs(dir_out)

    fpath = "gref/gv/" + par["fpath"].split("/")[-1] + ".gv"
    repl_graph(par, args, echo=False)

    name = fpath.split("/")[-1].replace(".gv", "")
    expression = f"dot -T{cmd}{' -Gdpi={}'.format(args[0]) if args else ''} {fpath} -o gref/{cmd}/{name}.{cmd}"

    printt(expression)
    os.system(expression)


def repl_table(par, args, echo=True):
    dct = par["data"]

    if echo is True:
        printt(wrap(" ".join(dct.keys()), 76))

    data = list()
    for x in dct.values():
        row = {
            "pmid": str(x["pmid"]),
            "title": str(x["title"]),
            "authors": "|".join(y[-1] for y in x["authors"]),
            "journal": str(x["journal"]),
            "date": str(x["date"]),
            "abstract": str(x["abstract"]),
            "references": "|".join(map(str, x["references"])),
            "citedin": "|".join(map(str, x["citedin"])),
            "five": "|".join(map(str, x["five"])),
        }
        data.append(row)


    dir_out = "gref/csv/"
    fpath = dir_out + par["fpath"].split("/")[-1] + ".csv"

    if not os.path.exists(dir_out):
        os.makedirs(dir_out)

    with open(fpath, "w", encoding="utf8", newline="") as fh:
        writer = csv.DictWriter(fh, data[0].keys())
        writer.writeheader()
        writer.writerows(data)

    return fpath


def repl_diwords(par, args):
    dct = par["data"]

    data = dict()
    for x in dct.values():
        words = tokenize(x["abstract"])
        diwords = map("{} {}".format, words, words[1:])
        for k, v in table(diwords).items():
            try:
                data[k] += v
            except KeyError:
                data[k] = v

    keys = sorted(data, key=data.get, reverse=True)

    dir_out = "gref/txt/"
    fpath = dir_out + par["fpath"].split("/")[-1] + ".txt"

    if not os.path.exists(dir_out):
        os.makedirs(dir_out)

    print("\n".join(map("{},{}".format, keys, map(data.get, keys))), file=open(fpath, "w", encoding="utf8"))
    printt("Wrote {}...".format(repr(fpath)))

    dct = dict()
    for k, v in data.items():
        w1, w2 = k.split()
        try:
            dct[w1][w2] = v
        except KeyError:
            dct[w1] = {w2: v}

    word = next(iter(dct))
    lst = [word]
    for i in range(int(args[0]) if args else 100):
        new = None
        while new is None:
            while new not in dct:
                new, *_ = random.choices(tuple(dct[word].keys()), weights=tuple(dct[word].values()))
            if not any((x in dct) for x in dct[new]):
                word = next(iter(dct))
                new = None
        word = new
        lst.append(word)
    printt(wrap(" ".join(lst), 76))

    return fpath


def repl_main():
    # parameters
    par = repl_par()

    # user shell
    printt("\nWelcome :-)")

    # main loop
    while par["state"] != State.EXIT:

        # save database (if applicable)
        if par["state"] == State.MAIN:
            repl_save(par)

        # prompt for input
        try:
            user_raw = input("\n{} > ".format("αδ"[par["state"]]))
        except KeyboardInterrupt:
            printe("\n\nPrompt killed!")
            user_raw = "exit"
        user = user_raw.split()
        printt()

        # user inputs nothing
        if not user:
            printe("No command provided! (try `HELP`)")
            continue

        # user inputs a command, but maybe not arguments
        cmd, *args = user
        cmd = cmd.upper()

        # function branches
        if cmd == "EXIT":
            par["state"] = State.EXIT

        elif cmd == "HELP":
            printt("Helping...")

        elif cmd == "SEARCH":
            if not args:
                printe("No query provided!")
                continue

            term = " ".join(args)
            repl_search(term)

        elif par["state"] == State.FILEIO:
            # overlapping checks and operations
            if cmd in ("ADD", "LOAD"):
                if not args:
                    printe("No filename provided!")
                    continue
                else:
                    dir_out = "gref/json/"
                    if not os.path.exists(dir_out):
                        os.makedirs(dir_out)

                    par["fpath"] = dir_out + args[0]
                    fpath_exists = os.path.exists(par["fpath"] + ".json")

            # otherwise
            if cmd == "ADD":
                if fpath_exists:
                    printe("Filepath already exists!")
                else:
                    printt("Making...")
                    par["data"] = dict()
                    par["state"] = State.MAIN

            elif cmd == "LOAD":
                if not fpath_exists:
                    printe("Filepath does not exist!")
                else:
                    printt("Loading...")
                    repl_load(par)
                    if par["data"] is None:
                        printe("Incompatible format!")
                    else:
                        par["state"] = State.MAIN

            elif cmd == "RM":
                dirs = os.listdir("gref")
                echo = True
                for x in dirs:
                    fpath = f"gref/{x}/{args[0]}.{x}"
                    if os.path.exists(fpath):
                        os.remove(fpath)
                        printt("Removed {}...".format(fpath))
                        echo = False
                if echo:
                    printe("File does not exist!")

            elif cmd == "PEEK":
                dpath = "gref/json"
                if not os.path.exists(dpath):
                    printe("No database found!")
                    continue

                files = os.listdir(dpath)
                if not files:
                    printe("No files found!")
                    continue

                files = sorted(x.replace(".json", "") for x in files)
                printt("\n  - ".join(("Files found:", *files)))
            else:
                printe("Unknown command!")

        elif par["state"] == State.MAIN:
            if cmd == "UNLOAD":
                printt("Unloading...")
                par = repl_par()
            elif cmd == "PEEK":
                printt("Peeking...")
                printt(len(par["data"]))
            elif cmd == "ADD":
                if not args:
                    printe("No PMIDs provided!")
                    continue

                printt("Adding...")
                repl_add(par, args)
            elif cmd == "GROW":
                if not args:
                    cycles = 1
                elif not args[0].isnumeric():
                    printe("Non-numeric argument!")
                    continue
                else:
                    cycles = int(args[0])

                printt("Growing...")
                try:
                    for i in range(cycles):
                        repl_grow(par)
                except KeyboardInterrupt:
                    printe("Aborted!")
            elif cmd == "GV":
                repl_graph(par, args)
            elif cmd in ("PNG", "SVG", "PDF"):
                repl_render(par, args, cmd)
            elif cmd == "CSV":
                repl_table(par, args)
            elif cmd == "TXT":
                try:
                    if not args:
                        pass
                    elif (args[0].upper() == "DIWORDS"):
                        _, *args = args
                        repl_diwords(par, args)
                except KeyboardInterrupt:
                    printe("Aborted!")
            else:
                printe("Unknown command!")


# SCRIPT


if __name__ == "__main__":
    repl_main()
