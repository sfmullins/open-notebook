import {
  Output,
  randomPassword,
  randomString,
  Services,
} from "~templates-utils";
import { Input } from "./meta";

export function generate(input: Input): Output {
  const services: Services = [];
  const appPassword = input.appPassword || randomPassword();
  const databasePassword = randomPassword();
  const encryptionKey = randomString(64);
  const databaseHost = `$(PROJECT_NAME)_${input.databaseServiceName}`;

  services.push({
    type: "app",
    data: {
      serviceName: input.databaseServiceName,
      env: [
        `POSTGRES_USER=open_notebook`,
        `POSTGRES_PASSWORD=${databasePassword}`,
        `POSTGRES_DB=open_notebook`,
      ].join("\n"),
      source: {
        type: "image",
        image: input.databaseServiceImage,
      },
      mounts: [
        {
          type: "volume",
          name: "postgres-data",
          mountPath: "/var/lib/postgresql/data",
        },
      ],
    },
  });

  services.push({
    type: "app",
    data: {
      serviceName: input.appServiceName,
      env: [
        `API_URL=https://$(PRIMARY_DOMAIN)`,
        `INTERNAL_API_URL=http://localhost:5055`,
        `OPEN_NOTEBOOK_ENCRYPTION_KEY=${encryptionKey}`,
        `OPEN_NOTEBOOK_PASSWORD=${appPassword}`,
        `DATABASE_URL=postgresql://open_notebook:${databasePassword}@${databaseHost}:5432/open_notebook`,
        `POSTGRES_URL=postgresql://open_notebook:${databasePassword}@${databaseHost}:5432/open_notebook`,
      ].join("\n"),
      source: {
        type: "image",
        image: input.appServiceImage,
      },
      domains: [
        {
          host: "$(EASYPANEL_DOMAIN)",
          port: 8502,
        },
      ],
      mounts: [
        {
          type: "volume",
          name: "notebook-data",
          mountPath: "/app/data",
        },
      ],
    },
  });

  return { services };
}
