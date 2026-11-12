
Near the top of each Python file, insert a comment that contains this
text: "NutrientConflictsEvaluation Project AGENTS.md APPLIED".

Design the programs such that, when any program is executed for the first time, the data must be put in cache and persisted to a designated location on disk. cache could be system cache/jvm cache by default. design for redis cache for future, configuration to be stored in application.properties file.

on subsequent run, first cache must be referred and if data does not exist, then read persisted result files (created from the very 1st run) and load/reload the data in cache. give an option to clear cache and delete persisted result files from previous run. 

The purpose to improve performance of response to subsequent queries. 

Do not edit the original data source folders, add paramember in application.properties file to take the external data source path.


When multiple interfaces CLI, API or GUI ) are to be exposed, write code query data and derive results such that same code can be reused for all interfaces.

