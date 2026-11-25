{{- define "agent-flow-mcp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agent-flow-mcp.fullname" -}}
{{- printf "%s" (include "agent-flow-mcp.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agent-flow-mcp.labels" -}}
app.kubernetes.io/name: {{ include "agent-flow-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
project: agent-flow-azure
{{- end -}}
