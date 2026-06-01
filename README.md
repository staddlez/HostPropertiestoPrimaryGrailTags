# Dynatrace Tag Cleanup + OneAgent Remote Config

Local Flask proxy UI. The browser never calls Dynatrace directly and never receives the full token.

## Run

```bash
cp .env.example .env
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## .env

```env
DT_ENV=https://hcaqa.live.dynatrace.com
DT_TOKEN=dt0c01.xxxxx
PORT=5000
```

## OneAgent remote config CSV examples

```csv
entityName,entityID,hostProperty,hostTag
here.testing.com,HOST-123145,"keyOne=value,keyTwo=value,keyThree=value","keyTag=valueTag"
```

```csv
entityName,entityID,hostProperty
here.testing.com,HOST-123145,"keyOne=value,keyTwo=value"
```

## OneAgent remote config tab

The tab now supports:

- Build payload only: parses CSV locally and shows the JSON payload.
- Validate payload: `POST /api/v2/oneagents/remoteConfigurationManagement/validator`.
- Dynatrace preview: `POST /api/v2/oneagents/remoteConfigurationManagement/preview`.
- Create job: `POST /api/v2/oneagents/remoteConfigurationManagement?restart=true|false`.
- Current job: `GET /api/v2/oneagents/remoteConfigurationManagement/current`.
- Finished jobs: `GET /api/v2/oneagents/remoteConfigurationManagement`.
- Job by ID: `GET /api/v2/oneagents/remoteConfigurationManagement/{id}`.

Allowed dynamic CSV attribute columns:

- `group`
- `hostGroup`
- `hostProperty`
- `hostTag`
- `networkZone`

## Remote config cleanup options

The OneAgent remote config tab includes optional value cleanup before payload creation:

- **Convert all key/value pairs to lowercase**: lowercases `key=value` entries for `set` operations.
- **Do not convert values for these keys**: comma-separated key list. Matching keys still get normalized, but the value stays exactly as supplied by the CSV.
- **Replace whitespace with underscores**: replaces whitespace in keys and values for `set` operations. Values for exception keys are preserved.

Example:

```csv
entityName,entityID,hostProperty
server1,HOST-123,"Business Owner=John Smith,appId=ABC 123"
```

With lowercase + whitespace replacement enabled and `appId` in the exception field, this becomes:

```json
{"attribute":"hostProperty","operation":"set","value":"business_owner=john_smith"}
{"attribute":"hostProperty","operation":"set","value":"appid=ABC 123"}
```
