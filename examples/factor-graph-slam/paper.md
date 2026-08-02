---
title: "Tightly-Coupled Wheel-Inertial Factor-Graph SLAM for Skid-Steer UGVs"
authors: ["A. Researcher", "B. Colleague"]
year: 2023
venue: "Journal of Field Robotics"
---

# Tightly-Coupled Wheel-Inertial Factor-Graph SLAM for Skid-Steer UGVs

*(Hand-authored example document. It is fictional, deliberately short, and exists
only to demonstrate deep knowledge extraction. It is not a real publication.)*

## Abstract

We present WIFG-SLAM, a tightly-coupled wheel-inertial state estimator for
skid-steer unmanned ground vehicles. The estimator is formulated as a factor
graph over robot poses and IMU biases, and is solved incrementally with iSAM2 in
GTSAM. A slip-aware wheel-odometry motion model compensates for the systematic
lateral slip that makes conventional differential-drive kinematics unusable on
skid-steer platforms. On the Rellis-3D off-road sequences WIFG-SLAM attains
1.8 % absolute trajectory error, compared to 4.6 % for a loosely-coupled EKF
baseline.

## 2. Problem formulation

We estimate the robot state at each keyframe *k*:

- the body pose in the world frame, `x_k ∈ SE(3)`;
- the IMU bias, `b_k ∈ R^6` (gyroscope and accelerometer).

Both are expressed in the world coordinate frame `W`; the IMU and wheel encoders
are rigidly mounted and expressed in the body frame `B`.

The maximum a posteriori estimate minimizes the sum of squared residuals

    X* = argmin_X  Σ_i || r_i(X) ||²_Σi

over all factors *i*, where `Σi` is the factor's noise covariance.

## 3. Factor graph

The graph contains three kinds of factors:

- **IMU preintegration factors** connecting consecutive poses and the bias
  variables. They follow the standard on-manifold preintegration formulation and
  use a Gaussian noise model derived from the IMU's continuous-time spectral
  densities.
- **Slip-aware wheel-odometry factors** connecting consecutive poses. The motion
  model augments differential-drive kinematics with a lateral slip term whose
  magnitude grows with yaw rate. We model its residual with a robust Huber loss
  rather than a plain Gaussian, because slip outliers are common on loose soil.
- **Prior factors** anchoring the first pose and the initial bias.

## 4. Sensors

The platform carries a 200 Hz MEMS IMU measuring angular velocity and specific
force, and quadrature wheel encoders measuring wheel angular velocity. No GNSS
or LiDAR is used.

## 5. Assumptions

The formulation assumes (a) the terrain is locally planar over one keyframe
interval, (b) IMU noise is zero-mean Gaussian and white, and (c) the wheel
radius is constant and known.

## 6. Results

Evaluated on Rellis-3D with absolute trajectory error (ATE) as the metric,
WIFG-SLAM achieves 1.8 % ATE. The loosely-coupled EKF baseline achieves 4.6 %.
Removing the slip term raises the error to 3.9 %, confirming that slip modelling
is the dominant contributor on skid-steer platforms.
