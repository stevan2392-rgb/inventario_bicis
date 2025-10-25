# inventario_bicis - Deploy to Netlify

Este repo contiene un sitio estático en la carpeta `docs/`.

Pasos rápidos para desplegar en Netlify:

1. Crear una cuenta en Netlify (https://app.netlify.com) y conectar con GitHub.
2. Sites → New site → Import from Git → seleccionar el repo `stevan2392-rgb/inventario_bicis`.
3. Configuración de build:
   - Branch to deploy: `main`
   - Base directory: (dejar vacío)
   - Build command: (dejar vacío si es un sitio estático)
   - Publish directory: `docs`
   - Functions directory: `netlify/functions` (si usas funciones)
4. Antes de deploy: en el campo "Site name" escribe `drjeasmanager` (si está disponible). Si no está disponible, Netlify pedirá otro nombre.
5. Haz click en Deploy site. La URL será `https://drjeasmanager.netlify.app` si el nombre está libre.

Netlify CLI (opcional):

- Instalar y loguear:
  npm i -g netlify-cli
  netlify login

- Crear sitio y hacer deploy desde la terminal:
  netlify sites:create --name drjeasmanager --dir=docs
  # o si el sitio ya fue creado:
  netlify deploy --dir=docs --prod

Notas importantes:
- Ya hay un archivo CNAME en la raíz del repo con `inventariobicis.tk`. Ese archivo solo afecta a GitHub Pages. No impide desplegar en Netlify. Si planeas usar el dominio en Netlify, configura el dominio en Netlify y sigue sus instrucciones DNS; puedes borrar o actualizar el CNAME según prefieras.
- Si necesitas que yo elimine/actualice el CNAME o suba más contenido al `docs/`, dímelo y lo hago.