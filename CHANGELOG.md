# Changelog

## [1.15.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.15.0...v1.15.1) (2026-11-28)


### Bug Fixes

* **deploy:** give the on-release catalogue job the catalogue's environment ([#180](https://github.com/JorisJonkers-dev/litellm/issues/180)) ([dd1a71f](https://github.com/JorisJonkers-dev/litellm/commit/dd1a71fde0d8f1ecf3d19c118c1d8d05dd2e62ec))

## [1.15.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.14.3...v1.15.0) (2026-11-28)


### Features

* **deploy:** sync the catalogue once after every release ([#177](https://github.com/JorisJonkers-dev/litellm/issues/177)) ([57e096f](https://github.com/JorisJonkers-dev/litellm/commit/57e096f109eb7c1b744cb2f87e67c840186e9d28))

## [1.14.3](https://github.com/JorisJonkers-dev/litellm/compare/v1.14.2...v1.14.3) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#172](https://github.com/JorisJonkers-dev/litellm/issues/172)) ([67f58fd](https://github.com/JorisJonkers-dev/litellm/commit/67f58fdfd38109bcc6bd24f39e8c8064789bdb92))

## [1.14.2](https://github.com/JorisJonkers-dev/litellm/compare/v1.14.1...v1.14.2) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#170](https://github.com/JorisJonkers-dev/litellm/issues/170)) ([3896823](https://github.com/JorisJonkers-dev/litellm/commit/3896823b5e05685422b17b24ec66c9dd25c32d73))

## [1.14.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.14.0...v1.14.1) (2026-11-27)


### Bug Fixes

* **config:** accept the free router step in the access-group test ([#168](https://github.com/JorisJonkers-dev/litellm/issues/168)) ([edde1fe](https://github.com/JorisJonkers-dev/litellm/commit/edde1fe795a6e063f94697d728444ea8a91b8fd4))
* **models:** refresh the generated model deployments ([#167](https://github.com/JorisJonkers-dev/litellm/issues/167)) ([330d893](https://github.com/JorisJonkers-dev/litellm/commit/330d8933f49935c96a1cb61e55f0f21371656d31))

## [1.14.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.11...v1.14.0) (2026-11-27)


### Features

* **catalog:** add OpenRouter's free router as the last tier-free step and label free deployments with their limit ([#164](https://github.com/JorisJonkers-dev/litellm/issues/164)) ([8b07594](https://github.com/JorisJonkers-dev/litellm/commit/8b075947eb80e88de3ae4cc4d02177f01e1f6a20))


### Bug Fixes

* **models:** refresh the generated model deployments ([#165](https://github.com/JorisJonkers-dev/litellm/issues/165)) ([7c33b13](https://github.com/JorisJonkers-dev/litellm/commit/7c33b13187901ef2dc1813d6d92e7d5d64120113))

## [1.13.11](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.10...v1.13.11) (2026-11-27)


### Bug Fixes

* **catalog:** give a free copy 300 s to finish and drop it after 60 s of silence ([#162](https://github.com/JorisJonkers-dev/litellm/issues/162)) ([f726698](https://github.com/JorisJonkers-dev/litellm/commit/f72669834035f23f976a49d944dc5afde55e3830))

## [1.13.10](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.9...v1.13.10) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#160](https://github.com/JorisJonkers-dev/litellm/issues/160)) ([294e4d0](https://github.com/JorisJonkers-dev/litellm/commit/294e4d0a4ca394bdc6ee07344f235fa65ae2d7fd))

## [1.13.9](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.8...v1.13.9) (2026-11-27)


### Bug Fixes

* **free-limits:** hold poe and verification blocks, match UTC-day and named resets ([#158](https://github.com/JorisJonkers-dev/litellm/issues/158)) ([8486983](https://github.com/JorisJonkers-dev/litellm/commit/84869832ec44c88d8b2d77a1c53385449b7b4ec2))

## [1.13.8](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.7...v1.13.8) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#156](https://github.com/JorisJonkers-dev/litellm/issues/156)) ([f61c116](https://github.com/JorisJonkers-dev/litellm/commit/f61c1163191e7ffd46b5444e723333aa5b5cf80b))

## [1.13.7](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.6...v1.13.7) (2026-11-27)


### Bug Fixes

* **free-limits:** block a forbidden or dead free copy for a fitting time and fail hung free calls fast ([#154](https://github.com/JorisJonkers-dev/litellm/issues/154)) ([66c0ab1](https://github.com/JorisJonkers-dev/litellm/commit/66c0ab1af1bb81cfc270786608be10c9e8cadcdf))

## [1.13.6](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.5...v1.13.6) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#152](https://github.com/JorisJonkers-dev/litellm/issues/152)) ([8d46eb4](https://github.com/JorisJonkers-dev/litellm/commit/8d46eb49f80bf4624fa070e0e1f2461b22b926c8))

## [1.13.5](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.4...v1.13.5) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#150](https://github.com/JorisJonkers-dev/litellm/issues/150)) ([3b1b38a](https://github.com/JorisJonkers-dev/litellm/commit/3b1b38a6795841be3560beb2fc3c57fb10f5303a))

## [1.13.4](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.3...v1.13.4) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#148](https://github.com/JorisJonkers-dev/litellm/issues/148)) ([ed14717](https://github.com/JorisJonkers-dev/litellm/commit/ed147175d2a65bd897ecf682ee751ba0d0a6ddca))

## [1.13.3](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.2...v1.13.3) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#146](https://github.com/JorisJonkers-dev/litellm/issues/146)) ([1bc530c](https://github.com/JorisJonkers-dev/litellm/commit/1bc530c73b009bcd24e63b5fc3c7f81c45d18939))

## [1.13.2](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.1...v1.13.2) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#144](https://github.com/JorisJonkers-dev/litellm/issues/144)) ([5069916](https://github.com/JorisJonkers-dev/litellm/commit/5069916437fb2836d53c4a436c86f647dd2220e1))

## [1.13.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.13.0...v1.13.1) (2026-11-27)


### Bug Fixes

* **models:** refresh the generated model deployments ([#142](https://github.com/JorisJonkers-dev/litellm/issues/142)) ([bb3fa33](https://github.com/JorisJonkers-dev/litellm/commit/bb3fa3354708a76cd42842765e1fe99384605cb9))

## [1.13.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.12.1...v1.13.0) (2026-11-27)


### Features

* **tiers:** the go-cheap and vibe tier keys reach paid/&lt;model&gt; ([#137](https://github.com/JorisJonkers-dev/litellm/issues/137)) ([79a4a99](https://github.com/JorisJonkers-dev/litellm/commit/79a4a99752b120b249356bba3793938f0caadfe8))


### Bug Fixes

* **catalog:** keep free ids out of paid groups and merge dot/hyphen version spellings of one release ([dba0baa](https://github.com/JorisJonkers-dev/litellm/commit/dba0baa7248955506d02cb6d584ad4ab9494b726))

## [1.12.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.12.0...v1.12.1) (2026-11-26)


### Bug Fixes

* **free-limits:** skip a free copy whose per-request input cap a request exceeds ([#118](https://github.com/JorisJonkers-dev/litellm/issues/118)) ([f04ee01](https://github.com/JorisJonkers-dev/litellm/commit/f04ee010cdc0656156a8e9f561fc056a6eb10d75))

## [1.12.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.11.0...v1.12.0) (2026-11-26)


### Features

* **catalog:** block deployments that real traffic shows are refused ([#113](https://github.com/JorisJonkers-dev/litellm/issues/113)) ([8cc877a](https://github.com/JorisJonkers-dev/litellm/commit/8cc877a3ecc836486e87ed942249ddb2660deac6))
* **catalog:** paid/&lt;model&gt; chains across the paid routes ([#116](https://github.com/JorisJonkers-dev/litellm/issues/116)) ([75f9b63](https://github.com/JorisJonkers-dev/litellm/commit/75f9b637c44edbd224ad36c594ce2646294d52ba))


### Bug Fixes

* **free-limits:** take dead free copies out instead of failing the group ([#112](https://github.com/JorisJonkers-dev/litellm/issues/112)) ([3080bc4](https://github.com/JorisJonkers-dev/litellm/commit/3080bc4ca10e74f71c180f93bf83c0801906b6e2))
* **models:** refresh the generated model deployments ([#115](https://github.com/JorisJonkers-dev/litellm/issues/115)) ([b5d1616](https://github.com/JorisJonkers-dev/litellm/commit/b5d161610393540827ffeb67ec177f5cfb11bbe5))

## [1.11.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.10.3...v1.11.0) (2026-11-26)


### Features

* **catalog:** free/&lt;model&gt; groups across every free provider, with limits before the call ([#106](https://github.com/JorisJonkers-dev/litellm/issues/106)) ([fbd5d40](https://github.com/JorisJonkers-dev/litellm/commit/fbd5d40256571ab6b0125975bc58782faddc9bd2))

## [1.10.3](https://github.com/JorisJonkers-dev/litellm/compare/v1.10.2...v1.10.3) (2026-11-26)


### Bug Fixes

* **models:** refresh the generated model deployments ([#103](https://github.com/JorisJonkers-dev/litellm/issues/103)) ([9c1e50b](https://github.com/JorisJonkers-dev/litellm/commit/9c1e50bf8096cd88323a201724d0ae346e2bd4e6))

## [1.10.2](https://github.com/JorisJonkers-dev/litellm/compare/v1.10.1...v1.10.2) (2026-11-26)


### Bug Fixes

* **catalog:** treat no-card free routes and zero-priced listings as free ([#99](https://github.com/JorisJonkers-dev/litellm/issues/99)) ([d9bb85b](https://github.com/JorisJonkers-dev/litellm/commit/d9bb85ba429bbf0dfb39690be70551ff62015b62))

## [1.10.1](https://github.com/JorisJonkers-dev/litellm/compare/v1.10.0...v1.10.1) (2026-11-26)


### Bug Fixes

* **models:** refresh the generated model deployments ([#97](https://github.com/JorisJonkers-dev/litellm/issues/97)) ([33fa69d](https://github.com/JorisJonkers-dev/litellm/commit/33fa69db3b8e41ae8bd7f57c7b9bdfb0c202e064))

## [1.10.0](https://github.com/JorisJonkers-dev/litellm/compare/v1.9.3...v1.10.0) (2026-11-26)


### Features

* **catalog:** decide usable from listings, models.dev and real traffic; no automatic probes ([#92](https://github.com/JorisJonkers-dev/litellm/issues/92)) ([1e389be](https://github.com/JorisJonkers-dev/litellm/commit/1e389be1d38937b572aee0c74eca31d96131e7dc))

## [1.9.3](https://github.com/JorisJonkers-dev/litellm/compare/v1.9.2...v1.9.3) (2026-11-25)


### Bug Fixes

* **models:** refresh the generated model deployments ([#80](https://github.com/JorisJonkers-dev/litellm/issues/80)) ([6821ac1](https://github.com/JorisJonkers-dev/litellm/commit/6821ac18738525c64d18c85e0c44dba2e8794d31))

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
