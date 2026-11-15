# Changelog

## [1.6.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.5.0...v1.6.0) (2026-11-14)


### Features

* **local:** route local/* to the GPU workstation's keyed gate ([#37](https://github.com/JorisJonkers-dev/litellm/issues/37)) ([40d903d](https://github.com/JorisJonkers-dev/litellm/commit/40d903dde06ac4cf2a3b1d34314b0dc57de9d844))


### Bug Fixes

* **ci:** cancel superseded runs and skip label events ([#28](https://github.com/JorisJonkers-dev/litellm/issues/28)) ([b3a2d80](https://github.com/JorisJonkers-dev/litellm/commit/b3a2d8092a136d5aeabe6e0fa3786c77ae4ff165))

## [1.5.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.4.0...v1.5.0) (2026-11-14)


### Features

* **telemetry:** export the route teams' budgets, and a probe that proves a budget refuses ([#35](https://github.com/JorisJonkers-dev/litellm/issues/35)) ([0658b98](https://github.com/JorisJonkers-dev/litellm/commit/0658b980f05edb992c4188e6813232e17b78a13e))

## [1.4.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.3.1...v1.4.0) (2026-11-14)


### Features

* **routing:** keep the free OpenRouter route public-only ([#33](https://github.com/JorisJonkers-dev/litellm/issues/33)) ([2161f83](https://github.com/JorisJonkers-dev/litellm/commit/2161f83a83aaf45e82bbde76ac3920a9069087ef))

## [1.3.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.3.0...v1.3.1) (2026-11-13)


### Bug Fixes

* **catalog:** open the generated-config PR as a fix so merging it releases ([#25](https://github.com/JorisJonkers-dev/litellm/issues/25)) ([7a5d466](https://github.com/JorisJonkers-dev/litellm/commit/7a5d46639d9e5699fe28df6954ebfbb7b40fa431))

## [1.3.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.2.0...v1.3.0) (2026-11-13)


### Features

* **catalog:** generated model routing config with per-account credentials ([#19](https://github.com/JorisJonkers-dev/litellm/issues/19)) ([0d8efeb](https://github.com/JorisJonkers-dev/litellm/commit/0d8efebe44bce69d65551f147abe5c243d9c49dc))

## [1.2.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.1.0...v1.2.0) (2026-11-13)


### Features

* **deploy:** serve LiteLLM anonymously at the edge and redirect the UI to SSO ([#17](https://github.com/JorisJonkers-dev/litellm/issues/17)) ([c895ed9](https://github.com/JorisJonkers-dev/litellm/commit/c895ed950d459dfa17e8502eee86189382e5684b))

## [1.1.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.0.3...v1.1.0) (2026-11-12)


### Features

* **deploy:** sign in to the admin UI through auth-api OIDC ([#14](https://github.com/JorisJonkers-dev/litellm/issues/14)) ([1bc7811](https://github.com/JorisJonkers-dev/litellm/commit/1bc781130ddfd7e6d5a5319632a9a573395de5f0))


### Bug Fixes

* **hygiene:** never cancel a run of the required hygiene checks ([#15](https://github.com/JorisJonkers-dev/litellm/issues/15)) ([2807355](https://github.com/JorisJonkers-dev/litellm/commit/2807355f962f5480e11637048fe084580b591ac7))

## [1.0.3](https://github.com/JorisJonkers-dev/litellm/compare/v1.0.2...v1.0.3) (2026-11-11)


### Bug Fixes

* **publish:** read the dashboards directory when validating the fragment ([#8](https://github.com/JorisJonkers-dev/litellm/issues/8)) ([f9a8a7b](https://github.com/JorisJonkers-dev/litellm/commit/f9a8a7b744a0433e9b05fef87aca30a3a5f6dc2b))

## [1.0.2](https://github.com/JorisJonkers-dev/litellm/compare/v1.0.1...v1.0.2) (2026-11-11)


### Bug Fixes

* **image:** pin the base to a versioned tag so the image names its LiteLLM version ([#6](https://github.com/JorisJonkers-dev/litellm/issues/6)) ([78e36a6](https://github.com/JorisJonkers-dev/litellm/commit/78e36a653b374157df7b77fb8474256cad390d6a))

## [1.0.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.0.0...v1.0.1) (2026-11-10)


### Bug Fixes

* **image:** run as a numeric non-root user ([#4](https://github.com/JorisJonkers-dev/litellm/issues/4)) ([61999af](https://github.com/JorisJonkers-dev/litellm/commit/61999af5c044c8f57bf5d621cb2e26c60a0ac420))

## 1.0.0 (2026-11-10)


### Features

* LiteLLM image with the estate model list and its deploy intent ([#2](https://github.com/JorisJonkers-dev/litellm/issues/2)) ([a7171d3](https://github.com/JorisJonkers-dev/litellm/commit/a7171d3ae01c6875b9c2df17c6424ee237b350ad))
