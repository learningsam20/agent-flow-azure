import { ApplicationInsights } from "@microsoft/applicationinsights-web";
import { ReactPlugin } from "@microsoft/applicationinsights-react-js";

const reactPlugin = new ReactPlugin();
let appInsights: ApplicationInsights | null = null;

export function initTelemetry(connectionString?: string | null) {
  if (!connectionString || appInsights) return reactPlugin;
  appInsights = new ApplicationInsights({
    config: {
      connectionString,
      enableAutoRouteTracking: true,
      extensions: [reactPlugin],
      extensionConfig: {
        [reactPlugin.identifier]: {},
      },
    },
  });
  appInsights.loadAppInsights();
  appInsights.trackPageView();
  return reactPlugin;
}

export function trackEvent(name: string, properties?: Record<string, string>) {
  appInsights?.trackEvent({ name }, properties);
}

export { reactPlugin };
