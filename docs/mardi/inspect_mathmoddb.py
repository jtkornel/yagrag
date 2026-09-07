"""Extract MathModDB ontology structure (classes, properties, labels) from the OWL export."""
import rdflib
from rdflib.namespace import RDF, RDFS, OWL

g = rdflib.Graph()
g.parse("docs/mardi/MathModDB.owl", format="xml")
print(f"triples: {len(g)}")

def label(s):
    for l in g.objects(s, RDFS.label):
        return str(l)
    return None

def comment(s):
    for l in g.objects(s, RDFS.comment):
        return str(l)
    return None

print("\n=== CLASSES ===")
for c in sorted(g.subjects(RDF.type, OWL.Class)):
    subs = [label(s) or str(s) for s in g.objects(c, RDFS.subClassOf)]
    print(f"{c}\n  label: {label(c)}\n  comment: {comment(c)}\n  subClassOf: {subs}")

print("\n=== OBJECT PROPERTIES ===")
for p in sorted(g.subjects(RDF.type, OWL.ObjectProperty)):
    dom = [label(d) or str(d) for d in g.objects(p, RDFS.domain)]
    rng = [label(r) or str(r) for r in g.objects(p, RDFS.range)]
    inv = [label(i) or str(i) for i in g.objects(p, OWL.inverseOf)]
    types = [str(t).split('#')[-1] for t in g.objects(p, RDF.type)]
    print(f"{p}\n  label: {label(p)}\n  types: {types}\n  domain: {dom}  range: {rng}  inverseOf: {inv}\n  comment: {comment(c)}")

print("\n=== DATATYPE PROPERTIES ===")
for p in sorted(g.subjects(RDF.type, OWL.DatatypeProperty)):
    dom = [label(d) or str(d) for d in g.objects(p, RDFS.domain)]
    rng = [str(r) for r in g.objects(p, RDFS.range)]
    print(f"{p}\n  label: {label(p)}\n  domain: {dom}  range: {rng}")

print("\n=== INDIVIDUAL COUNTS PER CLASS ===")
from collections import Counter
cnt = Counter()
for s, o in g.subject_objects(RDF.type):
    if (o, RDF.type, OWL.Class) in g:
        cnt[label(o) or str(o)] += 1
for k, v in cnt.most_common():
    print(f"  {k}: {v}")
