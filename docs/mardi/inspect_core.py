"""Show the core MathModDB relation properties in detail + a sample model individual."""
import rdflib
from rdflib.namespace import RDF, RDFS

g = rdflib.Graph()
g.parse("docs/mardi/MathModDB.owl", format="xml")

def label(s):
    for l in g.objects(s, RDFS.label):
        return str(l)
    return str(s).split('/')[-1]

CORE = ['P147','P1513','P1560','P1655','P1656','P1657','P1658','P1674','P1684',
        'P1691','P1692','P1736','P1869','P983','P989','P1459','P1463','P1275','P1587']
base = 'https://portal.mardi4nfdi.de/entity/'
for pid in CORE:
    p = rdflib.URIRef(base + pid)
    print(f"\n### {pid} {label(p)!r}")
    for s, o in g.subject_objects(p):
        print(f"   {label(s)}  ->  {label(o) if not isinstance(o, rdflib.Literal) else str(o)[:80]}")
        break  # one example each

# find a well-connected mathematical model and dump it fully
from collections import Counter

deg = Counter()
for s, p, o in g:
    if (s, RDF.type, rdflib.URIRef(base+'Q68663')) in g:
        deg[s] += 1
top = deg.most_common(3)
for m, d in top:
    print(f"\n\n===== MODEL {label(m)!r} (degree {d}) =====")
    for p, o in g.predicate_objects(m):
        pl = label(p)
        ol = str(o)[:100] if isinstance(o, rdflib.Literal) else label(o)
        print(f"  {pl}: {ol}")
