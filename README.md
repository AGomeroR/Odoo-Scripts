# Odoo_scripts

Scripts de mantenimiento de Odoo que **no forman parte del pipeline** del Importador (`../Importador/`).
Cada script se ejecuta a mano, de forma independiente.

> ⚠️ **Por defecto todos los scripts trabajan en PRODUCCIÓN** y escriben en ella, excepto `diagnostico_duplicados.py`, que solo lee.
> Antes de conectar a producción muestran un aviso con la URL y la base de datos y piden confirmación (`s`). Añade `--stg` para trabajar en staging.

## Puesta en marcha

```bash
cd /home/servidor/Programas-automatizaciones/Importador/Odoo_scripts
pip install -r requirements.txt     # o usar el venv del Importador: ../Importador/venv/bin/python
python <script>.py         # producción (pide confirmación)
python <script>.py --stg   # staging
```

Si las credenciales de Odoo del entorno elegido faltan o fallan, el script pide por consola URL, base de datos, usuario y API key (Enter mantiene el valor actual), reintenta y, cuando conecta, las guarda en el `.env`.

### Archivos de configuración

| Archivo | Uso |
|---|---|
| `.env` | Credenciales de Odoo de producción (`ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_API_KEY`) y de staging (las mismas con sufijo `_STG`), IA Ollama (`OPENAI_MODEL`, `OPENAI_API_URL`, `OPENAI_API_URL_DOCKER`, con los mismos valores que el `.env` del Importador; opcional `OPENAI_API_URL_HOST`) y `PROVEEDOR`. No se sube a git. |
| `.promptatributos` | Prompt de la IA para `Actualizar_atributos_publicados.py`. Es una copia del del Importador. |
| `logs/` | Cada script que usa `comun.py` escribe aquí su log con fecha y hora. |

## Scripts

### `comun.py`
No se ejecuta. Contiene lo que comparten los demás scripts:
- Carga el `.env` de esta carpeta y elige el entorno: producción, o staging si se pasa `--stg` (lo quita de `sys.argv`).
- `OdooClient`: conexión a Odoo por XML-RPC (`connect_to_odoo`: confirma producción, pide y guarda credenciales si fallan) y atajo `execute(modelo, método, args, kwargs)`.
- `configurar_log(nombre)` y `log_message(texto)`: log en consola y en `logs/<nombre>`.
- `normalizar(texto)`: minúsculas, sin tildes y sin espacios extra, para comparar nombres.
- `texto(valor)`: convierte un valor a texto limpio.

### `Actualizar_atributos_publicados.py`
Rellena con IA los **atributos de categoría** de los productos **ya publicados** en Odoo. Es la misma lógica que el script 23 del pipeline, que solo trata productos nuevos.

1. Crea en Odoo las categorías de atributo, los atributos (`create_variant = no_variant`) y los valores de `MODELOS` que falten.
2. Descarga en lotes de 200 todos los productos con `is_published = True`, con estos campos: nombre, categoría interna (`categ_id`), categorías eCommerce (`public_categ_ids`), `website_description` (sin HTML), `website_meta_description`, `x_studio_referencia_del_proveedor` y líneas de atributo. También lee la marca (atributo `Marca`/`Brand`).
3. Busca qué categorías del producto tienen una lista en `MODELOS`. Compara el último nivel de la categoría interna y de cada categoría eCommerce, y une sus atributos. Si un mismo atributo aparece en varias categorías, se le añade el nombre de la categoría entre paréntesis.
4. **Salta** el producto si:
   - ninguna de sus categorías tiene lista, o
   - ya tiene algún atributo distinto de `Marca`/`Brand`.
5. Pide a la IA un valor por atributo, usando nombre, marca, referencia y descripciones. Solo acepta valores de la lista permitida; si la IA no devuelve uno válido, ese atributo se deja en blanco.
6. Añade las líneas de atributo al producto. **Nunca borra ni modifica** líneas existentes, y la de marca no se toca.

- **Log:** `logs/actualizar_atributos_publicados_log.txt`.
- **Se puede relanzar:** los productos ya actualizados se saltan.
- **Duración:** una llamada a la IA por producto, así que puede tardar horas.
- **Importante:** `MODELOS` y las funciones de IA (`llamar_api`, `extraer_json`, `seleccionar_valores_con_ia`, `validar_respuesta`, `CategoryAttributeManager.sync_attributes`) son una **copia** de `../Importador/23.Atributos_categoria.py`. Si se cambian las listas en el script 23, hay que copiarlas aquí.

### `Sincronizar_categorias.py`
Hace que las **categorías internas** (`product.category`) se llamen exactamente igual que las **categorías eCommerce** (`product.public.category`), y asigna a cada producto publicado su categoría eCommerce.

1. Empareja las categorías cuya ruta completa coincide sin contar mayúsculas ni tildes, y **renombra la interna** con el nombre exacto de la eCommerce. Por ejemplo, `Guitarra eléctrica` pasa a `Guitarra Eléctrica`.
2. Renombra también las categorías de la tabla `RENOMBRAR`, que tienen distinto nombre en cada árbol:
   - `Cuerda Frotada / Cuerdas para instrumentos de cuerda frotada` → `Cuerdas`
   - `Guitarras y Bajos / Cuerdas` → `Cuerdas de Guitarra y Bajo`
   - `Pianos y Teclados / Accesorios & banquetas` → `Accesorios para Piano y Teclado`
3. Crea en eCommerce las categorías de `CREAR_EN_ECOMMERCE` (`Iluminación`).
4. Para cada producto publicado, en lotes de 200:
   - **añade** la categoría eCommerce cuya ruta coincide con su categoría interna;
   - **quita** solo las categorías eCommerce que son padres de esa (por ejemplo, `Guitarras y Bajos` cuando añade `Guitarras y Bajos / Guitarra Eléctrica`);
   - mantiene el resto (Outlet, Ofertas, otras categorías).
5. Al final muestra las categorías que siguen existiendo solo en uno de los dos árboles.

- **Log:** `logs/sincronizar_categorias_log.txt`.
- **Se puede relanzar:** lo que ya coincide no se vuelve a escribir.
- **Ojo:** el script copia la categoría interna a eCommerce. Si la categoría interna es incorrecta, el producto acaba con la categoría eCommerce correcta **y además** la incorrecta.

### `Sincronizar_categorias_tpv.py`
Rehace el árbol de **categorías TPV** (`pos.category`) para que sea idéntico al de categorías internas, que ya coincide con el de eCommerce.

1. Crea en TPV todas las categorías internas con el mismo nombre y jerarquía. Si ya existe una categoría TPV con la misma ruta exacta, la reutiliza. No crea las de `EXCLUIR` (Goods, Services, Expenses, Food, Deliveries, Amazon Services, Archivado, Generico) ni sus subcategorías.
2. A **todos** los productos (publicados o no, activos o archivados) les asigna en `pos_categ_ids` la categoría TPV equivalente a su categoría interna, **sustituyendo** las que tuvieran. Los productos de categorías excluidas se quedan sin categoría TPV.
3. **Elimina** todas las categorías TPV que no forman parte del nuevo árbol, empezando por las más profundas. Solo borra las que ya no tienen productos.

- **Log:** `logs/sincronizar_categorias_tpv_log.txt`.
- **Sesiones de TPV abiertas:** Odoo no deja borrar categorías TPV mientras haya alguna abierta ("No puede eliminar una categoría de punto de venta mientras una sesión aún está abierta"). En ese caso se registran como "no eliminadas" y el script termina con error. Basta con relanzarlo con todas las sesiones cerradas: reutiliza el árbol nuevo y solo borra las antiguas.

### `diagnostico_duplicados.py`
**Solo lectura.** Analiza los productos duplicados por `x_studio_referencia_del_proveedor`.

- **Parte 1:** lee las referencias de un `Articulos_a_subir.xlsx` (ruta fija en `EXCEL_PATH`, que apunta a un log de Zentralmedia del 2026-02-25; cámbiala para analizar otro archivo). Busca esas referencias en Odoo (solo productos activos) y cuenta cuáles aparecen una vez, duplicadas o no aparecen. Para los duplicados indica cuántos tienen como proveedor `PROVEEDOR` (del `.env`) y muestra hasta 10 ejemplos con ID, fecha de creación y proveedores.
- **Parte 2:** busca todas las referencias duplicadas en Odoo (solo productos activos; `limpiar_duplicados.py` sí incluye los archivados). Muestra el total de productos con referencia, las referencias duplicadas, los productos implicados y las copias sobrantes, además del top 15 de las más repetidas con sus nombres.

### `limpiar_duplicados.py`
**Borra o archiva** productos duplicados por `x_studio_referencia_del_proveedor`, incluidos los archivados.

```bash
python limpiar_duplicados.py --dry-run   # solo muestra lo que haría
python limpiar_duplicados.py             # aplica los cambios
python limpiar_duplicados.py --stg --dry-run   # igual, en staging
```

Para cada referencia duplicada **conserva el producto más reciente** (`create_date`). Con cada copia más antigua:
- **Si no tiene movimientos** (en `sale.order.line`, `purchase.order.line`, `stock.move` o `account.move.line`):
  1. elimina sus `stock.quant`;
  2. quita imágenes y desmarca venta, compra y TPV;
  3. **la borra definitivamente**.
- **Si tiene movimientos:**
  1. pone su stock a 0 con un ajuste de inventario;
  2. quita sus imágenes;
  3. **la archiva** (`active = False`, `website_published = False`) para conservar el historial.

Al final muestra un resumen de borrados, archivados, imágenes y stock limpiados, y errores. No escribe log en archivo.
