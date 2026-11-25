# Changelog

## [1.9.2](https://github.com/JorisJonkers-dev/litellm/compare/v1.9.1...v1.9.2) (2026-11-25)


### Bug Fixes

* **catalog:** read the published catalogue through the blob API once it passes 1 MB ([#78](https://github.com/JorisJonkers-dev/litellm/issues/78)) ([9e6b45f](https://github.com/JorisJonkers-dev/litellm/commit/9e6b45fa3b231488af02430f14b7ee66ef0c2b9a))

## [1.9.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.9.0...v1.9.1) (2026-11-19)


### Bug Fixes

* **catalog:** free per plan on Groq and Z.ai, clear probe reasons, Vault fields for provider settings ([#77](https://github.com/JorisJonkers-dev/litellm/issues/77)) ([1f6c3b8](https://github.com/JorisJonkers-dev/litellm/commit/1f6c3b8d1c846be25d8a4d6dae8447c4b21a34f2))
* **models:** refresh the generated model deployments ([#75](https://github.com/JorisJonkers-dev/litellm/issues/75)) ([abc49b1](https://github.com/JorisJonkers-dev/litellm/commit/abc49b1423d008e09967938ec4d33ad7587734d2))

## [1.9.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.8.0...v1.9.0) (2026-11-19)


### Features

* **catalog:** add first-wave free-only routes ([#66](https://github.com/JorisJonkers-dev/litellm/issues/66)) ([05861cf](https://github.com/JorisJonkers-dev/litellm/commit/05861cfafa76eb6581940bd1b5059c0e072a6470)), closes [#58](https://github.com/JorisJonkers-dev/litellm/issues/58) [#59](https://github.com/JorisJonkers-dev/litellm/issues/59)
* **catalog:** list free-route candidates for the owner to approve ([#71](https://github.com/JorisJonkers-dev/litellm/issues/71)) ([cdc33bc](https://github.com/JorisJonkers-dev/litellm/commit/cdc33bcf843e43980f27efa503da3db355a2a771))
* **catalog:** second-wave free routes ([#72](https://github.com/JorisJonkers-dev/litellm/issues/72)) ([0d79af8](https://github.com/JorisJonkers-dev/litellm/commit/0d79af8992fa7236a2376af333730647ed565752))
* **catalog:** shared free groups per model, and tier groups in routing.json ([#68](https://github.com/JorisJonkers-dev/litellm/issues/68)) ([5ba2081](https://github.com/JorisJonkers-dev/litellm/commit/5ba2081ae5f195be010a9bd846d25c154899d19f))
* **telemetry:** record requested tier against serving deployment, alert on a paid leak ([#67](https://github.com/JorisJonkers-dev/litellm/issues/67)) ([1e5c913](https://github.com/JorisJonkers-dev/litellm/commit/1e5c9136e479306bdc23ccbc1e0566b5c58e8a61)), closes [#62](https://github.com/JorisJonkers-dev/litellm/issues/62)


### Bug Fixes

* **catalog:** cap each paid account once, not once per model ([#70](https://github.com/JorisJonkers-dev/litellm/issues/70)) ([bbf8aa0](https://github.com/JorisJonkers-dev/litellm/commit/bbf8aa0c2149fc71ccb036190bd36f28de6f0db5))
* **catalog:** keep gemini-2.5-pro and gemini-2.5-flash out of the free groups ([#65](https://github.com/JorisJonkers-dev/litellm/issues/65)) ([1b43daa](https://github.com/JorisJonkers-dev/litellm/commit/1b43daa1d77934f6e69541193d1b9bce58aff622)), closes [#63](https://github.com/JorisJonkers-dev/litellm/issues/63)

## [1.8.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.7.0...v1.8.0) (2026-11-17)


### Features

* **catalog:** a free access group of every free model, with no paid fallback ([#47](https://github.com/JorisJonkers-dev/litellm/issues/47)) ([8348953](https://github.com/JorisJonkers-dev/litellm/commit/834895383d1cc3e07d832dea09fb91600a81e2d7))
* **catalog:** the general Mistral API keys serve only models no other route serves ([#49](https://github.com/JorisJonkers-dev/litellm/issues/49)) ([97b90ba](https://github.com/JorisJonkers-dev/litellm/commit/97b90bac61ec0781fcc22f14ed415edc7cbf919b))
* **opencode-go:** route OpenCode Go through LiteLLM's own opencode_go provider ([#53](https://github.com/JorisJonkers-dev/litellm/issues/53)) ([42a6c43](https://github.com/JorisJonkers-dev/litellm/commit/42a6c431d984cd739224c5a2388fea0b4f2da8f4))


### Bug Fixes

* **deploy:** raise the proxy's memory to 2Gi; 1Gi was OOMKilled ([#54](https://github.com/JorisJonkers-dev/litellm/issues/54)) ([295e817](https://github.com/JorisJonkers-dev/litellm/commit/295e817e6d56131e1ff6a0e75d2c48aaddc0da1b))

## [1.7.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.6.1...v1.7.0) (2026-11-15)


### Features

* **catalog:** make the free OpenRouter and OpenCode Go models usable, public work only ([#43](https://github.com/JorisJonkers-dev/litellm/issues/43)) ([f5c8a06](https://github.com/JorisJonkers-dev/litellm/commit/f5c8a068041cdc63a66ff943edf7bb7123145a29))

## [1.6.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.6.0...v1.6.1) (2026-11-15)


### Bug Fixes

* **catalog:** gate Claude deployments behind the anthropic access group ([#40](https://github.com/JorisJonkers-dev/litellm/issues/40)) ([a8a0735](https://github.com/JorisJonkers-dev/litellm/commit/a8a07351443e5e6b905c9dc6c811ec3b5c428e4a))

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
