/**
 * GENERATED FILE -- do not hand-edit.
 * Regenerate with: npm run generate:types (from frontend/).
 * Source: src/api/generated/terminal-frames.json.
 * See docs/maintaining-generated-types.md.
 */
export type paths = Record<string, never>;
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** PingFrame */
        PingFrame: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "ping";
            /**
             * V
             * @default 1
             * @constant
             */
            v: 1;
        };
        /** ResizeFrame */
        ResizeFrame: {
            /**
             * Cols
             * @default 80
             */
            cols: number;
            /**
             * Rows
             * @default 24
             */
            rows: number;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "resize";
            /**
             * V
             * @default 1
             * @constant
             */
            v: 1;
        };
        /**
         * ErrorClass
         * @description Classified terminal exit/setup errors surfaced to *this* terminal only.
         *
         *     Matches the WS control-frame ``error.class`` enum (FR-023):
         *     ``auth|network|remote_capability|missing_project|remote_launch``.
         * @enum {string}
         */
        ErrorClass: "auth" | "network" | "remote_capability" | "missing_project" | "remote_launch";
        /** ErrorFrame */
        ErrorFrame: {
            class: components["schemas"]["ErrorClass"];
            /** Message */
            message: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "error";
            /**
             * V
             * @default 1
             * @constant
             */
            v: 1;
        };
        /** ExitFrame */
        ExitFrame: {
            /** Code */
            code: number;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "exit";
            /**
             * V
             * @default 1
             * @constant
             */
            v: 1;
        };
        /** PongFrame */
        PongFrame: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "pong";
            /**
             * V
             * @default 1
             * @constant
             */
            v: 1;
        };
        /** ReadyFrame */
        ReadyFrame: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "ready";
            /**
             * V
             * @default 1
             * @constant
             */
            v: 1;
        };
        InboundFrame: components["schemas"]["ResizeFrame"] | components["schemas"]["PingFrame"];
        OutboundFrame: components["schemas"]["ReadyFrame"] | components["schemas"]["ExitFrame"] | components["schemas"]["ErrorFrame"] | components["schemas"]["PongFrame"];
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export type operations = Record<string, never>;
