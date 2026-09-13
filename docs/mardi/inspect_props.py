"""Dump all property usage in MathModDB: which P-properties connect which entity types."""
from collections import Counter, defaultdict

import rdflib
from rdflib.namespace import RDF, RDFS

g = rdflib.Graph()
g.parse("docs/mardi/MathModDB.owl", format="xml")

def label(s):
    for l in g.objects(s, RDFS.label):
        return str(l)
    return None

# class membership map
type_of = {}
for s, o in g.subject_objects(RDF.type):
    type_of.setdefault(s, set()).add(label(o) or str(o).split('/')[-1])

# property usage: prop -> Counter[(subject_types, object_types)]
usage = defaultdict(Counter)
prop_label = {}
for s, p, o in g:
    ps = str(p)
    if 'portal.mardi4nfdi.de/entity/P' in ps:
        prop_label[p] = label(p)
        st = ','.join(sorted(type_of.get(s, {'?'})))
        ot = ','.join(sorted(type_of.get(o, {'?'}))) if not isinstance(o, rdflib.Literal) else 'LITERAL'
        usage[p][(st, ot)] += 1

for p in sorted(usage, key=lambda p: str(p)):
    print(f"\n{p.split('/')[-1]}  label={prop_label[p]!r}")
    for (st, ot), n in usage[p].most_common(8):
        print(f"   {st}  ->  {ot}   ({n})")
