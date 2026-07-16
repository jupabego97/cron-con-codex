# ADR 0001: Monolito modular con procesamiento asíncrono

## Decisión

La plataforma inicia como un monolito modular Python. La API, recepción de webhooks y workers serán procesos desplegables por separado, pero compartirán contratos, migraciones y un repositorio.

## Motivo

El dominio todavía está consolidándose. Separar microservicios antes de contar con límites de dominio, carga real y equipos independientes aumentaría la complejidad operativa sin mejorar la calidad de la sincronización.

## Consecuencias

- Los módulos no pueden acceder directamente a tablas de otros módulos sin un contrato definido.
- Las tareas lentas o reintentables irán a una cola a partir de Fase 1.
- Se podrán separar servicios después si la carga, aislamiento o equipos lo justifican.

