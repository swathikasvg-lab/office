--
-- PostgreSQL database dump
--

\restrict Ss4ho2PvUGhGoArruUSdgoMJkdnWoUdCUfyDmpsKNwdIyLlcXBRyIhBSLDqJSIC

-- Dumped from database version 17.7 (Debian 17.7-3.pgdg13+1)
-- Dumped by pg_dump version 17.7 (Debian 17.7-3.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ops_users; Type: TABLE; Schema: public; Owner: autointelli
--

CREATE TABLE public.ops_users (
    id integer NOT NULL,
    username character varying(80) NOT NULL,
    password_hash character varying(255) NOT NULL,
    customer_id integer,
    is_admin boolean,
    is_active boolean
);


ALTER TABLE public.ops_users OWNER TO autointelli;

--
-- Name: ops_users_id_seq; Type: SEQUENCE; Schema: public; Owner: autointelli
--

CREATE SEQUENCE public.ops_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ops_users_id_seq OWNER TO autointelli;

--
-- Name: ops_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: autointelli
--

ALTER SEQUENCE public.ops_users_id_seq OWNED BY public.ops_users.id;


--
-- Name: permissions; Type: TABLE; Schema: public; Owner: autointelli
--

CREATE TABLE public.permissions (
    id integer NOT NULL,
    code character varying(128) NOT NULL,
    description character varying(255)
);


ALTER TABLE public.permissions OWNER TO autointelli;

--
-- Name: permissions_id_seq; Type: SEQUENCE; Schema: public; Owner: autointelli
--

CREATE SEQUENCE public.permissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.permissions_id_seq OWNER TO autointelli;

--
-- Name: permissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: autointelli
--

ALTER SEQUENCE public.permissions_id_seq OWNED BY public.permissions.id;


--
-- Name: role_permissions; Type: TABLE; Schema: public; Owner: autointelli
--

CREATE TABLE public.role_permissions (
    role_id integer NOT NULL,
    permission_id integer NOT NULL
);


ALTER TABLE public.role_permissions OWNER TO autointelli;

--
-- Name: roles; Type: TABLE; Schema: public; Owner: autointelli
--

CREATE TABLE public.roles (
    id integer NOT NULL,
    name character varying(64) NOT NULL,
    description character varying(255)
);


ALTER TABLE public.roles OWNER TO autointelli;

--
-- Name: roles_id_seq; Type: SEQUENCE; Schema: public; Owner: autointelli
--

CREATE SEQUENCE public.roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.roles_id_seq OWNER TO autointelli;

--
-- Name: roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: autointelli
--

ALTER SEQUENCE public.roles_id_seq OWNED BY public.roles.id;


--
-- Name: user_roles; Type: TABLE; Schema: public; Owner: autointelli
--

CREATE TABLE public.user_roles (
    user_id integer NOT NULL,
    role_id integer NOT NULL
);


ALTER TABLE public.user_roles OWNER TO autointelli;

--
-- Name: ops_users id; Type: DEFAULT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.ops_users ALTER COLUMN id SET DEFAULT nextval('public.ops_users_id_seq'::regclass);


--
-- Name: permissions id; Type: DEFAULT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.permissions ALTER COLUMN id SET DEFAULT nextval('public.permissions_id_seq'::regclass);


--
-- Name: roles id; Type: DEFAULT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.roles ALTER COLUMN id SET DEFAULT nextval('public.roles_id_seq'::regclass);


--
-- Data for Name: ops_users; Type: TABLE DATA; Schema: public; Owner: autointelli
--

COPY public.ops_users (id, username, password_hash, customer_id, is_admin, is_active) FROM stdin;
1	admin	scrypt:32768:8:1$RlTMu4DFc3TTSk6a$8f6f48c6445f91133f5348cd1057529424405e4c07cb51a75c0532e5f543423c77d03533c7c270195fe39b85c0896366369e5cda334925aae79c416e06ad4fb8	\N	t	t
5	anand	scrypt:32768:8:1$S2ytUQR8yXWUaHiN$c877e5ceb7f136f45cef66c9ffc62dfd903e9c632f8206a37e315ad159979685f72e90cbf0322c855a1b75cca8e1e5af04d65a29c347b6310390782719702287	\N	t	f
6	aiadmin	scrypt:32768:8:1$ZCyABPMJMmpdETzD$51924eb82b1f553a02d51f4209dd6046c7566aff5316507e28c6937e38e5eac7b3522be8b25176151099815a1894064514b8113c774eb06e9fae28ca865c365f	\N	f	t
7	auisy_guest	scrypt:32768:8:1$JjtLPgPlHJpXMkMv$3009fcc35321ea51f52c112dd536771407bac5980b63d69230769890caf5c32ff42e1cad660c21369a8067cafdcc077a1b2d7268baf41c1aa77aefdb65474641	7	f	t
4	cust1	scrypt:32768:8:1$Seu3ByboCgnlQoT3$13d929e7a97525660a2dd6624cd681bdfa8d111400c913384e1a33cf5fc8222e32caac3c70eb7c9766026630c6073760388941ee1a9626b4495a91465026a77e	3	f	f
3	readonly	scrypt:32768:8:1$YVNfm03c5IarwINT$838515384e736761122ebcc497a6cfe0496142d34e7136b987e031136e159bfc1bd545bb73b9fcd88ea302724df144e6e437006c255fc40edd1beadf4375fd2a	\N	f	f
2	nocuser	scrypt:32768:8:1$4QJKsyuIHl42Y7J2$b468a2b6902603eb57d29a61ecb1c7b1835d40cac6eeaaa0202c1f99011b937e58abb917afbca3d37d3d73599fbb8d2fbd6bec5faedd2c8cc8a22d3200e9e128	\N	f	f
\.


--
-- Data for Name: permissions; Type: TABLE DATA; Schema: public; Owner: autointelli
--

COPY public.permissions (id, code, description) FROM stdin;
1	view_servers	View server monitoring
2	edit_snmp	Manage SNMP devices
3	edit_contacts	Manage contacts & groups
4	view_reports	Access reports
5	manage_alerts	Create/update alert rules
6	manage_users	Add/remove users
7	view_monitoring	View monitoring pages
9	view_tools	View tools
10	view_admin	View admin pages
16	view_desktops	View desktop monitoring
17	view_idrac	View iDRAC monitoring
18	view_ilo	View HP iLO monitoring
19	view_urls	View URL monitoring
20	view_link	View link monitoring
21	view_snmp	View SNMP monitoring
22	view_ping	View ping monitoring
23	view_ports	View port monitoring
24	view_discovery	View discovery
25	view_alerts	View Alert Rules
\.


--
-- Data for Name: role_permissions; Type: TABLE DATA; Schema: public; Owner: autointelli
--

COPY public.role_permissions (role_id, permission_id) FROM stdin;
2	1
2	2
2	3
2	5
3	1
4	1
4	6
5	4
5	7
5	1
5	17
5	18
5	19
5	20
5	21
5	22
5	23
5	24
5	25
3	7
\.


--
-- Data for Name: roles; Type: TABLE DATA; Schema: public; Owner: autointelli
--

COPY public.roles (id, name, description) FROM stdin;
1	admin	Full system admin
2	noc	NOC team – manage monitoring
4	devops	Manage servers, integrations
5	FULL_VIEWER	Read-only access to entire system
3	customer	Read-only dashboard access
\.


--
-- Data for Name: user_roles; Type: TABLE DATA; Schema: public; Owner: autointelli
--

COPY public.user_roles (user_id, role_id) FROM stdin;
2	2
3	3
4	3
5	2
6	5
7	3
\.


--
-- Name: ops_users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: autointelli
--

SELECT pg_catalog.setval('public.ops_users_id_seq', 7, true);


--
-- Name: permissions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: autointelli
--

SELECT pg_catalog.setval('public.permissions_id_seq', 25, true);


--
-- Name: roles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: autointelli
--

SELECT pg_catalog.setval('public.roles_id_seq', 6, true);


--
-- Name: ops_users ops_users_pkey; Type: CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.ops_users
    ADD CONSTRAINT ops_users_pkey PRIMARY KEY (id);


--
-- Name: ops_users ops_users_username_key; Type: CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.ops_users
    ADD CONSTRAINT ops_users_username_key UNIQUE (username);


--
-- Name: permissions permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT permissions_pkey PRIMARY KEY (id);


--
-- Name: role_permissions role_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_pkey PRIMARY KEY (role_id, permission_id);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_id);


--
-- Name: ix_ops_users_customer_id; Type: INDEX; Schema: public; Owner: autointelli
--

CREATE INDEX ix_ops_users_customer_id ON public.ops_users USING btree (customer_id);


--
-- Name: ix_permissions_code; Type: INDEX; Schema: public; Owner: autointelli
--

CREATE UNIQUE INDEX ix_permissions_code ON public.permissions USING btree (code);


--
-- Name: ix_roles_name; Type: INDEX; Schema: public; Owner: autointelli
--

CREATE UNIQUE INDEX ix_roles_name ON public.roles USING btree (name);


--
-- Name: ops_users ops_users_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.ops_users
    ADD CONSTRAINT ops_users_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(cid) ON DELETE SET NULL;


--
-- Name: role_permissions role_permissions_permission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES public.permissions(id) ON DELETE CASCADE;


--
-- Name: role_permissions role_permissions_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE CASCADE;


--
-- Name: user_roles user_roles_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE CASCADE;


--
-- Name: user_roles user_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.ops_users(id) ON DELETE CASCADE;

--
-- Name: licenses; Type: TABLE; Schema: public; Owner: autointelli
--

CREATE TABLE public.licenses (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    name character varying(120),
    starts_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    grace_days integer NOT NULL,
    status character varying(20) NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


ALTER TABLE public.licenses OWNER TO autointelli;

--
-- Name: licenses_id_seq; Type: SEQUENCE; Schema: public; Owner: autointelli
--

CREATE SEQUENCE public.licenses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.licenses_id_seq OWNER TO autointelli;

--
-- Name: licenses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: autointelli
--

ALTER SEQUENCE public.licenses_id_seq OWNED BY public.licenses.id;

--
-- Name: license_items; Type: TABLE; Schema: public; Owner: autointelli
--

CREATE TABLE public.license_items (
    id integer NOT NULL,
    license_id integer NOT NULL,
    monitoring_type character varying(64) NOT NULL,
    max_count integer NOT NULL
);


ALTER TABLE public.license_items OWNER TO autointelli;

--
-- Name: license_items_id_seq; Type: SEQUENCE; Schema: public; Owner: autointelli
--

CREATE SEQUENCE public.license_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.license_items_id_seq OWNER TO autointelli;

--
-- Name: license_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: autointelli
--

ALTER SEQUENCE public.license_items_id_seq OWNED BY public.license_items.id;

--
-- Name: licenses id; Type: DEFAULT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.licenses ALTER COLUMN id SET DEFAULT nextval('public.licenses_id_seq'::regclass);

--
-- Name: license_items id; Type: DEFAULT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.license_items ALTER COLUMN id SET DEFAULT nextval('public.license_items_id_seq'::regclass);

--
-- Name: licenses licenses_pkey; Type: CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.licenses
    ADD CONSTRAINT licenses_pkey PRIMARY KEY (id);

--
-- Name: license_items license_items_pkey; Type: CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.license_items
    ADD CONSTRAINT license_items_pkey PRIMARY KEY (id);

--
-- Name: ix_licenses_customer_id; Type: INDEX; Schema: public; Owner: autointelli
--

CREATE INDEX ix_licenses_customer_id ON public.licenses USING btree (customer_id);

--
-- Name: ix_license_items_license_id; Type: INDEX; Schema: public; Owner: autointelli
--

CREATE INDEX ix_license_items_license_id ON public.license_items USING btree (license_id);

--
-- Name: ix_license_items_monitoring_type; Type: INDEX; Schema: public; Owner: autointelli
--

CREATE INDEX ix_license_items_monitoring_type ON public.license_items USING btree (monitoring_type);

--
-- Name: licenses licenses_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.licenses
    ADD CONSTRAINT licenses_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(cid) ON DELETE CASCADE;

--
-- Name: license_items license_items_license_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: autointelli
--

ALTER TABLE ONLY public.license_items
    ADD CONSTRAINT license_items_license_id_fkey FOREIGN KEY (license_id) REFERENCES public.licenses(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict Ss4ho2PvUGhGoArruUSdgoMJkdnWoUdCUfyDmpsKNwdIyLlcXBRyIhBSLDqJSIC

