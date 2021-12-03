__author__ = "Shane Drabing"
__license__ = "MIT"
__email__ = "shane.drabing@gmail.com"


# IMPORTS


import functools
import json
import os
import random
import re
import statistics
import sys
import time

import bs4
import requests


# CONSTANTS


FORM_NCBI = "https://{}.ncbi.nlm.nih.gov/{}".format
FORM_EUTILS = FORM_NCBI("eutils", "entrez/eutils/{}.cgi").format
TUP_LINKNAME = ("pubmed_pubmed_citedin", "pubmed_pubmed_five")


# ENUMS


class State:
    INIT, LOOP, EXIT = range(3)


# FUNCTIONS (GENENRAL)


def table(itr):
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
        "Date: " + dct["date"],
        "Title: " + dct["title"],
        "By: " + lnames, "~",
        "Abstract: " + dct["abstract"].replace('"', "'"), "~",
        "PMID: " + dct["pmid"],
        "Journal: " + dct["journal"]
    ))


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
        "state": State.INIT,
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
                    if x in dct:
                        edges.add((x, k))
            if "citedin" in v:
                for x in v["citedin"]:
                    if x in dct:
                        edges.add((k, x))

    nodes = set()
    for x in map(set, edges):
        nodes |= x
    nodes = tuple(map(dct.get, sorted(nodes)))

    graph = """
    digraph {{

    pad=0.7
    layout=dot
    rankdir=BT
    ranksep=2.1
    nodesep=0.04
    splines=true
    outputorder=edgesfirst

    node [shape=note style=filled fillcolor=none target="_blank"]
    edge [arrowhead=none]

    {}

    {}

    }}
    """.lstrip().format(
        "\n    ".join(map(lambda x: "{} [label=\"{}\" href=\"{}\" tooltip=\"{}\"]".format(
            x["pmid"],
            "{}\n({})".format(wrap(", ".join([xx[-1].split(",")[0] for xx in x["authors"]][:5]), 20), x["date"]),
            "https://pubmed.ncbi.nlm.nih.gov/{}/".format(x["pmid"]),
            article_summary_wide(x).replace('"', "'")
        ), nodes)),
        "\n    ".join(map(lambda x: "{}:n->{}:s".format(*x), sorted(edges)))
    )

    dir_out = "gref/gv/"
    if not os.path.exists(dir_out):
        os.makedirs(dir_out)

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


def repl_main():
    # parameters
    par = repl_par()

    # user shell
    printt("\nWelcome :-)")

    # main loop
    while par["state"] != State.EXIT:

        # save database (if applicable)
        if par["state"] == State.LOOP:
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

        elif par["state"] == State.INIT:
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
                    par["state"] = State.LOOP
            elif cmd == "LOAD":
                if not fpath_exists:
                    printe("Filepath does not exist!")
                else:
                    printt("Loading...")
                    repl_load(par)
                    if par["data"] is None:
                        printe("Incompatible format!")
                    else:
                        par["state"] = State.LOOP
            elif cmd == "RM":
                dirs = os.listdir("gref")
                for x in dirs:
                    fpath = f"gref/{x}/{args[0]}.{x}"
                    if os.path.exists(fpath):
                        os.remove(fpath)
                        printt("Removed {}...".format(fpath))
            else:
                printe("Unknown command!")

        elif par["state"] == State.LOOP:
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
            elif cmd == "GRAPH":
                repl_graph(par, args)
            elif cmd in ("PNG", "SVG", "PDF"):
                repl_render(par, args, cmd)
            elif cmd == "RENDER":
                for typ in ("PNG", "SVG", "PDF"):
                    repl_render(par, args, typ)
            else:
                printe("Unknown command!")


# SCRIPT


if __name__ == "__main__":
    repl_main()
