# Agents

Guidance for any coding agent working in this repository.

- Estate conventions: https://github.com/JorisJonkers-dev/workspace/blob/main/CLAUDE.md
- Repository overview: [README.md](README.md)

The LiteLLM proxy image for the estate: route list in `config/config.yaml`, image in `Dockerfile`, deploy intent in `deploy/`, model catalogue sync in `catalog/`.

## Build and test

Scripts are defined in `package.json`; Node 24 or newer is required.

- All tests: npm test
- Catalogue tests only: npm run test:catalog
- Image test only: npm run test:image
