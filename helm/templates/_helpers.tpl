{{- define "finmind.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "finmind.namespace" -}}
{{- default "finmind" .Values.namespace -}}
{{- end -}}

{{- define "finmind.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: finmind
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
