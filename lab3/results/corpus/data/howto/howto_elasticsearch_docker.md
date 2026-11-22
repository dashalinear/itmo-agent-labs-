# Инструкция: как поднять Elasticsearch в Docker для учебного стенда

1. Установите Docker Desktop и убедитесь, что команда docker ps работает.
2. Создайте docker-compose.yml с образом elasticsearch, режимом single-node
   и отключённой безопасностью (только для локального стенда!).
3. Ограничьте память JVM: ES_JAVA_OPTS=-Xms512m -Xmx512m.
4. Запустите docker compose up -d и дождитесь статуса yellow или green:
   curl http://localhost:9200/_cluster/health
5. Создайте индекс с явным mapping: keyword для идентификаторов и путей,
   text с анализатором russian для текста.

Типичные проблемы:
- контейнер падает с ошибкой vm.max_map_count — увеличьте параметр в WSL;
- порт 9200 занят — остановите другой экземпляр Elasticsearch.
