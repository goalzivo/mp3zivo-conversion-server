MP3ZIVO CONVERSIONSERVER

Deze backend converteert openbare, directe mediabestands-URL's waarvoor je de benodigde rechten hebt.
Hij is bewust geen algemene streaming-platform downloader. Dat voorkomt dat je site een dienst aanbiedt die platformregels of auteursrechten kan omzeilen.

Deploy:
1. Upload/deploy deze map als Cloud Run service.
2. Stel environment variable CONVERTER_API_KEY in op een lange willekeurige waarde.
3. Geef de service URL door aan de WordPress-plugin.
4. Health check: GET /health
5. Convert: POST /convert met JSON {"url":"https://voorbeeld.nl/bestand.mp4","format":"mp3"}.
Header: X-Converter-Key: <zelfde sleutel>

Let op: Cloud Run heeft een request/compute-limiet en tijdelijke opslag. Voor zeer grote video's is een dedicated worker/storage-oplossing beter.
