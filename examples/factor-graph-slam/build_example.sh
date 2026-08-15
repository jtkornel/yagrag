#!/usr/bin/env bash
# Worked example: capture one paper's *internals* as structured knowledge.
#
# This script performs, by hand, exactly what the agent skills in
# `.agents/skills/` prescribe — ingest, deep knowledge extraction (entities +
# reified claims), index, search — but with the LLM reasoning already "frozen"
# into the literal commands below. It is therefore both documentation and an
# executable end-to-end smoke test (see `tests/test_e2e_smoke.py`).
#
# Usage:  ./examples/factor-graph-slam/build_example.sh <target-kb-dir>
#
# The `KB` environment variable overrides how the CLI is invoked, e.g.
#   KB=".venv/bin/python -m kb.cli.main" ./examples/factor-graph-slam/build_example.sh /tmp/demo-kb
set -euo pipefail

KB=${KB:-kb}
EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXAMPLE_DIR/../.." && pwd)"

if [ $# -ne 1 ]; then
    echo "usage: $0 <target-kb-dir>" >&2
    exit 2
fi
TARGET="$1"

# `command` keeps this wrapper from recursing into itself when $KB is `kb`.
kb() { command $KB "$@"; }

# --- 1. a knowledge base with the seed domain schema ------------------------
kb init "$TARGET" --name factor-graph-slam-example >/dev/null

# Offline/CI runs can select the deterministic `hash` embedder instead of
# downloading a local model.
if [ -n "${KB_EMBEDDER_BACKEND:-}" ]; then
    sed -i "s/^backend = .*/backend = \"$KB_EMBEDDER_BACKEND\"/" "$TARGET/kb.toml"
fi

cp "$REPO_ROOT/schema/migrations/0001_seed_domain.gql" "$TARGET/schema/migrations/"
kb schema apply --kb "$TARGET" >/dev/null
kb schema validate --kb "$TARGET" >/dev/null

# --- 2. ingest the raw source (immutable) -----------------------------------
# `doc add` prints the assigned id; with --json it is machine-readable. The
# first raw document in a fresh KB is deterministically `raw-0001`.
kb doc add "$EXAMPLE_DIR/paper.md" --kind raw \
    --title "Tightly-Coupled Wheel-Inertial Factor-Graph SLAM for Skid-Steer UGVs" \
    --tag factor-graphs --tag slam --tag ugv \
    --kb "$TARGET" --json >/dev/null
DOC=raw-0001

# Provenance shorthand reused by every write below.
P='"origin": "raw", "sources": ["'$DOC'"]'

# --- 3. the document node (bibliographic layer) -----------------------------
kb graph upsert-node Document --kb "$TARGET" --props '{
  "id": "'$DOC'",
  "name": "Tightly-Coupled Wheel-Inertial Factor-Graph SLAM for Skid-Steer UGVs",
  "kind": "raw", "format": "md", "year": 2023,
  '"$P"'
}' >/dev/null

kb graph upsert-node Author --kb "$TARGET" \
    --props '{"id": "author-a-researcher", "name": "A. Researcher", '"$P"'}' >/dev/null
kb graph upsert-edge AUTHORED_BY --kb "$TARGET" \
    --from Document:$DOC --to Author:author-a-researcher --props '{'"$P"'}' >/dev/null

# --- 4. deep knowledge layer: the internals of the paper --------------------
# THIS is the point of the exercise. A summary of the paper would be a failed
# extraction; what we want is the structured content it asserts.

# 4a. The method / estimator and the factor graph it is built on.
kb graph upsert-node Method --kb "$TARGET" --props '{
  "id": "wifg-slam", "name": "WIFG-SLAM",
  "summary": "Tightly-coupled wheel-inertial factor-graph SLAM for skid-steer UGVs.",
  '"$P"'}' >/dev/null
kb graph upsert-node StateEstimator --kb "$TARGET" --props '{
  "id": "wifg-slam-estimator", "name": "WIFG-SLAM incremental smoother",
  "summary": "Incremental MAP smoother over poses and IMU biases.", '"$P"'}' >/dev/null
kb graph upsert-node FactorGraph --kb "$TARGET" --props '{
  "id": "wifg-slam-graph", "name": "WIFG-SLAM factor graph",
  "summary": "Poses and IMU biases connected by preintegration, wheel-odometry and prior factors.",
  '"$P"'}' >/dev/null

# 4b. Variables — the unknowns actually being estimated.
kb graph upsert-node Variable --kb "$TARGET" --props '{
  "id": "var-body-pose", "name": "body pose x_k",
  "domain": "SE(3)", "dimension": 6, '"$P"'}' >/dev/null
kb graph upsert-node Variable --kb "$TARGET" --props '{
  "id": "var-imu-bias", "name": "IMU bias b_k",
  "domain": "R^6", "dimension": 6, '"$P"'}' >/dev/null

# 4c. Factors — the constraints, with their types.
kb graph upsert-node Factor --kb "$TARGET" --props '{
  "id": "factor-imu-preintegration", "name": "IMU preintegration factor",
  "factor_type": "between", '"$P"'}' >/dev/null
kb graph upsert-node Factor --kb "$TARGET" --props '{
  "id": "factor-slip-wheel-odometry", "name": "Slip-aware wheel-odometry factor",
  "factor_type": "between", '"$P"'}' >/dev/null
kb graph upsert-node Factor --kb "$TARGET" --props '{
  "id": "factor-pose-prior", "name": "Pose prior factor",
  "factor_type": "prior", '"$P"'}' >/dev/null

# 4d. Models, noise, solver, sensors, frames.
kb graph upsert-node MotionModel --kb "$TARGET" --props '{
  "id": "slip-aware-diff-drive", "name": "Slip-aware differential-drive model",
  "summary": "Differential-drive kinematics plus a lateral slip term growing with yaw rate.",
  '"$P"'}' >/dev/null
kb graph upsert-node NoiseModel --kb "$TARGET" --props '{
  "id": "noise-huber", "name": "Huber robust loss", '"$P"'}' >/dev/null
kb graph upsert-node NoiseModel --kb "$TARGET" --props '{
  "id": "noise-imu-gaussian", "name": "Gaussian IMU noise from spectral densities",
  '"$P"'}' >/dev/null
kb graph upsert-node Solver --kb "$TARGET" --props '{
  "id": "isam2", "name": "iSAM2", "summary": "Incremental smoothing and mapping solver.",
  '"$P"'}' >/dev/null
kb graph upsert-node Tool --kb "$TARGET" --props '{
  "id": "gtsam", "name": "GTSAM", '"$P"'}' >/dev/null
kb graph upsert-node Sensor --kb "$TARGET" --props '{
  "id": "mems-imu", "name": "200 Hz MEMS IMU", "modality": "inertial", '"$P"'}' >/dev/null
kb graph upsert-node Sensor --kb "$TARGET" --props '{
  "id": "wheel-encoders", "name": "Quadrature wheel encoders", "modality": "odometry",
  '"$P"'}' >/dev/null
kb graph upsert-node Quantity --kb "$TARGET" --props '{
  "id": "angular-velocity", "name": "angular velocity", "symbol": "omega",
  "unit": "rad/s", '"$P"'}' >/dev/null
kb graph upsert-node CoordinateFrame --kb "$TARGET" --props '{
  "id": "frame-world", "name": "World frame W", '"$P"'}' >/dev/null

# 4e. Equations and assumptions — the paper's explicit formal content.
kb graph upsert-node Equation --kb "$TARGET" --props '{
  "id": "eq-map-objective", "name": "MAP objective",
  "latex": "X^* = \\arg\\min_X \\sum_i \\lVert r_i(X) \\rVert^2_{\\Sigma_i}",
  '"$P"'}' >/dev/null
kb graph upsert-node Assumption --kb "$TARGET" --props '{
  "id": "assume-locally-planar", "name": "Terrain is locally planar per keyframe interval",
  '"$P"'}' >/dev/null
kb graph upsert-node Assumption --kb "$TARGET" --props '{
  "id": "assume-white-gaussian-imu", "name": "IMU noise is zero-mean white Gaussian",
  '"$P"'}' >/dev/null

# 4f. Evaluation context.
kb graph upsert-node Dataset --kb "$TARGET" --props '{
  "id": "rellis-3d", "name": "Rellis-3D", '"$P"'}' >/dev/null
kb graph upsert-node Metric --kb "$TARGET" --props '{
  "id": "ate", "name": "Absolute trajectory error", '"$P"'}' >/dev/null
kb graph upsert-node Robot --kb "$TARGET" --props '{
  "id": "skid-steer-ugv", "name": "Skid-steer UGV", '"$P"'}' >/dev/null
kb graph upsert-node Task --kb "$TARGET" --props '{
  "id": "task-slam", "name": "Simultaneous localization and mapping", '"$P"'}' >/dev/null

# --- 5. wire the domain entities together -----------------------------------
edge() { kb graph upsert-edge "$1" --from "$2" --to "$3" --kb "$TARGET" --props '{'"$P"'}' >/dev/null; }

edge HAS_VARIABLE FactorGraph:wifg-slam-graph Variable:var-body-pose
edge HAS_VARIABLE FactorGraph:wifg-slam-graph Variable:var-imu-bias
edge HAS_FACTOR   FactorGraph:wifg-slam-graph Factor:factor-imu-preintegration
edge HAS_FACTOR   FactorGraph:wifg-slam-graph Factor:factor-slip-wheel-odometry
edge HAS_FACTOR   FactorGraph:wifg-slam-graph Factor:factor-pose-prior

edge CONNECTS Factor:factor-imu-preintegration  Variable:var-body-pose
edge CONNECTS Factor:factor-imu-preintegration  Variable:var-imu-bias
edge CONNECTS Factor:factor-slip-wheel-odometry Variable:var-body-pose
edge CONNECTS Factor:factor-pose-prior          Variable:var-body-pose

edge HAS_NOISE Factor:factor-slip-wheel-odometry NoiseModel:noise-huber
edge HAS_NOISE Factor:factor-imu-preintegration  NoiseModel:noise-imu-gaussian
edge HAS_NOISE Sensor:mems-imu                   NoiseModel:noise-imu-gaussian

edge ESTIMATES  StateEstimator:wifg-slam-estimator Variable:var-body-pose
edge ESTIMATES  StateEstimator:wifg-slam-estimator Variable:var-imu-bias
edge USES       StateEstimator:wifg-slam-estimator MotionModel:slip-aware-diff-drive
edge USES       Method:wifg-slam                   FactorGraph:wifg-slam-graph
edge ASSUMES    Method:wifg-slam                   Assumption:assume-locally-planar
edge ASSUMES    Method:wifg-slam                   Assumption:assume-white-gaussian-imu
edge SOLVED_BY  FactorGraph:wifg-slam-graph        Solver:isam2
edge OPTIMIZES  Solver:isam2                       FactorGraph:wifg-slam-graph
edge IMPLEMENTS Tool:gtsam                         Method:wifg-slam
edge DEFINED_BY Factor:factor-slip-wheel-odometry  Equation:eq-map-objective
edge MEASURES   Sensor:mems-imu                    Quantity:angular-velocity
edge EXPRESSED_IN Variable:var-body-pose           CoordinateFrame:frame-world
edge EVALUATED_ON Method:wifg-slam                 Dataset:rellis-3d
edge EVALUATED_ON Method:wifg-slam                 Metric:ate
edge APPLIES_TO   Method:wifg-slam                 Task:task-slam
edge PART_OF      Sensor:mems-imu                  Robot:skid-steer-ugv

# --- 6. link the document to what it defines and mentions -------------------
edge DEFINES  Document:$DOC Method:wifg-slam
edge DEFINES  Document:$DOC FactorGraph:wifg-slam-graph
edge DEFINES  Document:$DOC MotionModel:slip-aware-diff-drive
edge MENTIONS Document:$DOC Solver:isam2
edge MENTIONS Document:$DOC Tool:gtsam
edge MENTIONS Document:$DOC Dataset:rellis-3d
edge MENTIONS Document:$DOC Robot:skid-steer-ugv

# --- 7. reified claims: the assertions, with confidence and provenance ------
# A claim is a first-class node so that competing assertions from different
# sources can coexist and be reconciled later, instead of being flattened.
kb graph upsert-claim claim-wifg-ate-rellis --kb "$TARGET" \
    --subject Method:wifg-slam \
    --predicate achieves_ate_on \
    --object Dataset:rellis-3d \
    --props '{"name": "WIFG-SLAM achieves 1.8% ATE on Rellis-3D",
              "qualifiers": "value=1.8%; metric=ATE; sequences=off-road",
              "confidence": 0.9, '"$P"'}' >/dev/null

kb graph upsert-claim claim-ekf-baseline-ate --kb "$TARGET" \
    --subject Method:wifg-slam \
    --predicate outperforms_baseline \
    --object-literal "loosely-coupled EKF: 4.6% ATE vs 1.8% ATE" \
    --props '{"name": "WIFG-SLAM outperforms a loosely-coupled EKF baseline",
              "qualifiers": "dataset=Rellis-3D; metric=ATE",
              "confidence": 0.85, '"$P"'}' >/dev/null

kb graph upsert-claim claim-slip-term-dominant --kb "$TARGET" \
    --subject MotionModel:slip-aware-diff-drive \
    --predicate is_dominant_error_contributor_for \
    --object Robot:skid-steer-ugv \
    --props '{"name": "Slip modelling dominates accuracy on skid-steer platforms",
              "qualifiers": "ablation: removing slip term raises ATE 1.8% -> 3.9%",
              "confidence": 0.8, '"$P"'}' >/dev/null

# The document is the evidence for each claim.
edge SUPPORTS Document:$DOC Claim:claim-wifg-ate-rellis
edge SUPPORTS Document:$DOC Claim:claim-ekf-baseline-ate
edge SUPPORTS Document:$DOC Claim:claim-slip-term-dominant

# --- 8. statically checkable formal content ------------------------------------
# Equations and algorithms are stored in programming-language syntax as real
# files under `code/`, so `kb code check` can validate them statically without
# any LLM round-trip. `eq-map-objective` above deliberately keeps LaTeX only:
# a code representation is an enrichment, never a precondition for ingestion.

# The symbols the SymPy expression uses must exist in the graph, otherwise
# symbol consistency reports them as warnings.
kb graph upsert-node Quantity --kb "$TARGET" --props '{
  "id": "lateral-slip-velocity", "name": "lateral slip velocity", "symbol": "v_y",
  "unit": "m/s", '"$P"'}' >/dev/null
kb graph upsert-node Quantity --kb "$TARGET" --props '{
  "id": "slip-coefficient", "name": "slip coefficient", "symbol": "alpha",
  "unit": "m", '"$P"'}' >/dev/null

mkdir -p "$TARGET/code/equations" "$TARGET/code/algorithms"

# 8a. A SymPy canonical form: the paper's lateral-slip relation.
cat > "$TARGET/code/equations/lateral_slip.sympy" <<'SYMPY'
# Lateral slip velocity grows linearly with commanded yaw rate.
# Symbols: v_y (lateral slip velocity), alpha (slip coefficient), omega (yaw rate).
Eq(v_y, alpha * omega)
SYMPY

kb graph upsert-node Equation --kb "$TARGET" --props '{
  "id": "eq-lateral-slip", "name": "Lateral slip relation",
  "latex": "v_y = \\alpha\\,\\omega",
  "code_language": "sympy",
  "code_path": "code/equations/lateral_slip.sympy",
  "code_entry": "eq_lateral_slip",
  '"$P"'}' >/dev/null

edge DEFINED_BY MotionModel:slip-aware-diff-drive Equation:eq-lateral-slip
edge DEFINES    Document:$DOC                     Equation:eq-lateral-slip

# 8b. A Python reference implementation of the slip-aware odometry increment.
cat > "$TARGET/code/algorithms/slip_aware_odometry.py" <<'PYTHON'
"""Slip-aware differential-drive odometry increment.

Reference implementation of the motion model used by the wheel-odometry
factor: differential-drive kinematics plus a lateral slip term proportional
to the commanded yaw rate.
"""

import numpy as np


def integrate(v: float, omega: float, alpha: float, dt: float) -> np.ndarray:
    """Return the body-frame increment [dx, dy, dtheta] over `dt` seconds."""
    return np.array([v * dt, alpha * omega * dt, omega * dt])
PYTHON

kb graph upsert-node Algorithm --kb "$TARGET" --props '{
  "id": "slip-aware-odometry-increment", "name": "Slip-aware odometry increment",
  "summary": "Body-frame pose increment from wheel velocities with a lateral slip term.",
  "code_language": "python",
  "code_path": "code/algorithms/slip_aware_odometry.py",
  "code_entry": "integrate",
  '"$P"'}' >/dev/null

edge IMPLEMENTS Algorithm:slip-aware-odometry-increment Method:wifg-slam
edge MENTIONS   Document:$DOC Algorithm:slip-aware-odometry-increment

# Static checking only: nothing stored here is ever executed. A failing
# snippet is recorded as `code_status: failed` and never blocks a write, so
# this command exits 0 either way.
kb code check --kb "$TARGET" --lint >/dev/null

# --- 9. make it retrievable -------------------------------------------------
kb index build --kb "$TARGET" >/dev/null

echo "Example knowledge base built at: $TARGET"
echo
echo "Try:"
echo "  kb search 'slip aware wheel odometry' --kb $TARGET"
echo "  kb graph query 'MATCH (f:FactorGraph)-[:HAS_FACTOR]->(x:Factor)-[:CONNECTS]->(v:Variable) RETURN f.name, x.name, v.name' --kb $TARGET"
echo "  kb code list --kb $TARGET"
echo "  kb code show Algorithm:slip-aware-odometry-increment --kb $TARGET"
