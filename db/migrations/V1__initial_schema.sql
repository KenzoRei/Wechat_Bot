\encoding UTF8
-- ============================================================
-- V1: Initial Schema (consolidated baseline, squashed 2026-08-05)
-- Logistics WeChat Bot Platform
--
-- Dumped directly from the live database via pg_dump --schema-only, so
-- this reflects exactly what was actually running (V1-V13's cumulative
-- effect), not a hand-transcribed approximation. Previous incremental
-- migration history (V1-V13) is not kept as files -- git log has them if
-- ever needed.
-- ============================================================

--
-- PostgreSQL database dump
--


-- Dumped from database version 18.4 (Debian 18.4-1.pgdg12+1)
-- Dumped by pg_dump version 18.3

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

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: generate_serial_number(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.generate_serial_number() RETURNS character varying
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN 'REQ-' ||
           TO_CHAR(now(), 'YYYYMMDD') || '-' ||
           LPAD(nextval('request_serial_seq')::TEXT, 6, '0');
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: conversation_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_session (
    session_id uuid DEFAULT gen_random_uuid() NOT NULL,
    wechat_openid character varying(128) NOT NULL,
    group_id uuid NOT NULL,
    service_type_id uuid,
    status character varying(30) DEFAULT 'active'::character varying NOT NULL,
    conversation_history jsonb DEFAULT '[]'::jsonb NOT NULL,
    collected_fields jsonb DEFAULT '{}'::jsonb NOT NULL,
    request_log_id uuid,
    expires_at timestamp with time zone DEFAULT (now() + '01:00:00'::interval) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversation_session_status_check CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'pending_confirmation'::character varying, 'completed'::character varying, 'cancelled'::character varying, 'rejected'::character varying, 'timed_out'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: group_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.group_config (
    group_id uuid DEFAULT gen_random_uuid() NOT NULL,
    wechat_group_id character varying(128) NOT NULL,
    description character varying(500),
    is_active boolean DEFAULT true NOT NULL,
    daily_request_limit integer,
    context jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    group_robot_webhook_url text
);


--
-- Name: group_member; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.group_member (
    wechat_openid character varying(128) NOT NULL,
    group_id uuid NOT NULL,
    role_id uuid NOT NULL,
    display_name character varying(200),
    is_active boolean DEFAULT true NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    warehouse_code character varying(20)
);


--
-- Name: group_service; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.group_service (
    group_id uuid NOT NULL,
    service_type_id uuid NOT NULL,
    workflow_id uuid NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: group_service_role; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.group_service_role (
    group_id uuid NOT NULL,
    service_type_id uuid NOT NULL,
    role_id uuid NOT NULL,
    created_by character varying(128) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: interaction_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interaction_log (
    interaction_id uuid DEFAULT gen_random_uuid() NOT NULL,
    wechat_openid character varying(128) NOT NULL,
    group_id uuid,
    intent character varying(30) NOT NULL,
    intent_type character varying(20) NOT NULL,
    service_type_id uuid,
    request_log_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: request_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_log (
    log_id uuid DEFAULT gen_random_uuid() NOT NULL,
    serial_number character varying(30) DEFAULT public.generate_serial_number() NOT NULL,
    wechat_openid character varying(128) NOT NULL,
    group_id uuid,
    service_type_id uuid,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    raw_message text NOT NULL,
    parsed_input jsonb DEFAULT '{}'::jsonb NOT NULL,
    result jsonb,
    error_detail text,
    wechat_msg_id character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT request_log_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'success'::character varying, 'failed'::character varying, 'cancelled'::character varying, 'timed_out'::character varying, 'stale'::character varying])::text[])))
);


--
-- Name: request_serial_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.request_serial_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: role; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role (
    role_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(20) NOT NULL,
    description character varying(200),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: service_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_type (
    service_type_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    description character varying(500),
    input_schema jsonb DEFAULT '{}'::jsonb NOT NULL,
    group_config_schema jsonb DEFAULT '{}'::jsonb NOT NULL,
    confirmation_note text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    requires_confirmation boolean DEFAULT true NOT NULL,
    targets_existing_request boolean DEFAULT false NOT NULL,
    awaits_completion boolean DEFAULT false NOT NULL,
    keywords jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: uchoice_address; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uchoice_address (
    address_id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_name character varying(200),
    charge_type character varying(20) NOT NULL,
    addr text NOT NULL,
    warehouse_code character varying(20),
    note text,
    created_by character varying(128) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    destination_warehouse_code character varying(20),
    CONSTRAINT uchoice_address_charge_type_check CHECK (((charge_type)::text = ANY ((ARRAY['short_delivery'::character varying, 'delivery'::character varying, 'truck_transfer'::character varying, 'self_pickup'::character varying])::text[])))
);


--
-- Name: uchoice_sku; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uchoice_sku (
    sku_code character varying(50) NOT NULL,
    description character varying(200) NOT NULL
);


--
-- Name: uchoice_storage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uchoice_storage (
    warehouse_code character varying(20) NOT NULL,
    sku_code character varying(50) NOT NULL,
    boxes_per_pallet integer NOT NULL,
    pallet_count integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT uchoice_storage_pallet_count_check CHECK ((pallet_count >= 0))
);


--
-- Name: uchoice_storage_fee_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uchoice_storage_fee_ledger (
    ledger_id uuid DEFAULT gen_random_uuid() NOT NULL,
    warehouse_code character varying(20) NOT NULL,
    fee_date date NOT NULL,
    pallet_count integer NOT NULL,
    storage_fee numeric(10,2) NOT NULL
);


--
-- Name: uchoice_storage_txn; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uchoice_storage_txn (
    txn_id uuid DEFAULT gen_random_uuid() NOT NULL,
    warehouse_code character varying(20) NOT NULL,
    sku_code character varying(50) NOT NULL,
    boxes_per_pallet integer NOT NULL,
    pallet_delta integer NOT NULL,
    txn_type character varying(20) NOT NULL,
    request_log_id uuid,
    note text,
    created_by character varying(128) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT uchoice_storage_txn_txn_type_check CHECK (((txn_type)::text = ANY ((ARRAY['inbound'::character varying, 'outbound'::character varying, 'convert_in'::character varying, 'convert_out'::character varying, 'move_in'::character varying, 'move_out'::character varying, 'adjust'::character varying, 'recount'::character varying, 'transfer_in'::character varying, 'transfer_out'::character varying])::text[])))
);


--
-- Name: workflow; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow (
    workflow_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: workflow_step; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_step (
    step_id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_id uuid NOT NULL,
    step_order smallint NOT NULL,
    step_type character varying(100) NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: conversation_session conversation_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_session
    ADD CONSTRAINT conversation_session_pkey PRIMARY KEY (session_id);


--
-- Name: group_config group_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_config
    ADD CONSTRAINT group_config_pkey PRIMARY KEY (group_id);


--
-- Name: group_config group_config_wechat_group_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_config
    ADD CONSTRAINT group_config_wechat_group_id_key UNIQUE (wechat_group_id);


--
-- Name: group_member group_member_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_member
    ADD CONSTRAINT group_member_pkey PRIMARY KEY (wechat_openid, group_id);


--
-- Name: group_service group_service_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_service
    ADD CONSTRAINT group_service_pkey PRIMARY KEY (group_id, service_type_id);


--
-- Name: group_service_role group_service_role_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_service_role
    ADD CONSTRAINT group_service_role_pkey PRIMARY KEY (group_id, service_type_id, role_id);


--
-- Name: interaction_log interaction_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interaction_log
    ADD CONSTRAINT interaction_log_pkey PRIMARY KEY (interaction_id);


--
-- Name: request_log request_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_log
    ADD CONSTRAINT request_log_pkey PRIMARY KEY (log_id);


--
-- Name: role role_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role
    ADD CONSTRAINT role_name_key UNIQUE (name);


--
-- Name: role role_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role
    ADD CONSTRAINT role_pkey PRIMARY KEY (role_id);


--
-- Name: service_type service_type_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_type
    ADD CONSTRAINT service_type_name_key UNIQUE (name);


--
-- Name: service_type service_type_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_type
    ADD CONSTRAINT service_type_pkey PRIMARY KEY (service_type_id);


--
-- Name: uchoice_address uchoice_address_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchoice_address
    ADD CONSTRAINT uchoice_address_pkey PRIMARY KEY (address_id);


--
-- Name: uchoice_sku uchoice_sku_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchoice_sku
    ADD CONSTRAINT uchoice_sku_pkey PRIMARY KEY (sku_code);


--
-- Name: uchoice_storage_fee_ledger uchoice_storage_fee_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchoice_storage_fee_ledger
    ADD CONSTRAINT uchoice_storage_fee_ledger_pkey PRIMARY KEY (ledger_id);


--
-- Name: uchoice_storage_fee_ledger uchoice_storage_fee_ledger_warehouse_code_fee_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchoice_storage_fee_ledger
    ADD CONSTRAINT uchoice_storage_fee_ledger_warehouse_code_fee_date_key UNIQUE (warehouse_code, fee_date);


--
-- Name: uchoice_storage uchoice_storage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchoice_storage
    ADD CONSTRAINT uchoice_storage_pkey PRIMARY KEY (warehouse_code, sku_code, boxes_per_pallet);


--
-- Name: uchoice_storage_txn uchoice_storage_txn_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchoice_storage_txn
    ADD CONSTRAINT uchoice_storage_txn_pkey PRIMARY KEY (txn_id);


--
-- Name: workflow workflow_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow
    ADD CONSTRAINT workflow_name_key UNIQUE (name);


--
-- Name: workflow workflow_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow
    ADD CONSTRAINT workflow_pkey PRIMARY KEY (workflow_id);


--
-- Name: workflow_step workflow_step_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_step
    ADD CONSTRAINT workflow_step_pkey PRIMARY KEY (step_id);


--
-- Name: workflow_step workflow_step_workflow_id_step_order_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_step
    ADD CONSTRAINT workflow_step_workflow_id_step_order_key UNIQUE (workflow_id, step_order);


--
-- Name: idx_group_member_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_group_member_group ON public.group_member USING btree (group_id);


--
-- Name: idx_group_member_openid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_group_member_openid ON public.group_member USING btree (wechat_openid);


--
-- Name: idx_group_service_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_group_service_group ON public.group_service USING btree (group_id);


--
-- Name: idx_interaction_log_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_interaction_log_created ON public.interaction_log USING btree (created_at DESC);


--
-- Name: idx_interaction_log_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_interaction_log_group ON public.interaction_log USING btree (group_id);


--
-- Name: idx_interaction_log_openid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_interaction_log_openid ON public.interaction_log USING btree (wechat_openid);


--
-- Name: idx_request_log_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_log_created ON public.request_log USING btree (created_at DESC);


--
-- Name: idx_request_log_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_log_group ON public.request_log USING btree (group_id);


--
-- Name: idx_request_log_msg_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_request_log_msg_id ON public.request_log USING btree (wechat_msg_id) WHERE (wechat_msg_id IS NOT NULL);


--
-- Name: idx_request_log_openid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_log_openid ON public.request_log USING btree (wechat_openid);


--
-- Name: idx_request_log_serial; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_request_log_serial ON public.request_log USING btree (serial_number);


--
-- Name: idx_request_log_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_log_status ON public.request_log USING btree (status);


--
-- Name: idx_session_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_session_expires ON public.conversation_session USING btree (expires_at) WHERE ((status)::text = ANY ((ARRAY['active'::character varying, 'pending_confirmation'::character varying])::text[]));


--
-- Name: idx_session_one_active_per_user; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_session_one_active_per_user ON public.conversation_session USING btree (wechat_openid, group_id) WHERE ((status)::text = ANY ((ARRAY['active'::character varying, 'pending_confirmation'::character varying])::text[]));


--
-- Name: idx_session_openid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_session_openid ON public.conversation_session USING btree (wechat_openid);


--
-- Name: idx_uchoice_storage_txn_bucket; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uchoice_storage_txn_bucket ON public.uchoice_storage_txn USING btree (warehouse_code, sku_code, boxes_per_pallet);


--
-- Name: idx_uchoice_storage_txn_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uchoice_storage_txn_created ON public.uchoice_storage_txn USING btree (created_at DESC);


--
-- Name: idx_uchoice_storage_txn_request; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uchoice_storage_txn_request ON public.uchoice_storage_txn USING btree (request_log_id);


--
-- Name: idx_workflow_step; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_step ON public.workflow_step USING btree (workflow_id, step_order);


--
-- Name: conversation_session conversation_session_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_session
    ADD CONSTRAINT conversation_session_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.group_config(group_id) ON DELETE CASCADE;


--
-- Name: conversation_session conversation_session_service_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_session
    ADD CONSTRAINT conversation_session_service_type_id_fkey FOREIGN KEY (service_type_id) REFERENCES public.service_type(service_type_id) ON DELETE SET NULL;


--
-- Name: conversation_session fk_session_request_log; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_session
    ADD CONSTRAINT fk_session_request_log FOREIGN KEY (request_log_id) REFERENCES public.request_log(log_id) ON DELETE SET NULL;


--
-- Name: group_member group_member_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_member
    ADD CONSTRAINT group_member_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.group_config(group_id) ON DELETE CASCADE;


--
-- Name: group_member group_member_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_member
    ADD CONSTRAINT group_member_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.role(role_id) ON DELETE RESTRICT;


--
-- Name: group_service group_service_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_service
    ADD CONSTRAINT group_service_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.group_config(group_id) ON DELETE CASCADE;


--
-- Name: group_service_role group_service_role_group_id_service_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_service_role
    ADD CONSTRAINT group_service_role_group_id_service_type_id_fkey FOREIGN KEY (group_id, service_type_id) REFERENCES public.group_service(group_id, service_type_id) ON DELETE CASCADE;


--
-- Name: group_service_role group_service_role_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_service_role
    ADD CONSTRAINT group_service_role_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.role(role_id) ON DELETE CASCADE;


--
-- Name: group_service group_service_service_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_service
    ADD CONSTRAINT group_service_service_type_id_fkey FOREIGN KEY (service_type_id) REFERENCES public.service_type(service_type_id) ON DELETE CASCADE;


--
-- Name: group_service group_service_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_service
    ADD CONSTRAINT group_service_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflow(workflow_id) ON DELETE RESTRICT;


--
-- Name: interaction_log interaction_log_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interaction_log
    ADD CONSTRAINT interaction_log_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.group_config(group_id) ON DELETE SET NULL;


--
-- Name: interaction_log interaction_log_request_log_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interaction_log
    ADD CONSTRAINT interaction_log_request_log_id_fkey FOREIGN KEY (request_log_id) REFERENCES public.request_log(log_id) ON DELETE SET NULL;


--
-- Name: interaction_log interaction_log_service_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interaction_log
    ADD CONSTRAINT interaction_log_service_type_id_fkey FOREIGN KEY (service_type_id) REFERENCES public.service_type(service_type_id) ON DELETE SET NULL;


--
-- Name: request_log request_log_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_log
    ADD CONSTRAINT request_log_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.group_config(group_id) ON DELETE SET NULL;


--
-- Name: request_log request_log_service_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_log
    ADD CONSTRAINT request_log_service_type_id_fkey FOREIGN KEY (service_type_id) REFERENCES public.service_type(service_type_id) ON DELETE SET NULL;


--
-- Name: uchoice_storage uchoice_storage_sku_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchoice_storage
    ADD CONSTRAINT uchoice_storage_sku_code_fkey FOREIGN KEY (sku_code) REFERENCES public.uchoice_sku(sku_code);


--
-- Name: uchoice_storage_txn uchoice_storage_txn_request_log_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uchoice_storage_txn
    ADD CONSTRAINT uchoice_storage_txn_request_log_id_fkey FOREIGN KEY (request_log_id) REFERENCES public.request_log(log_id) ON DELETE SET NULL;


--
-- Name: workflow_step workflow_step_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_step
    ADD CONSTRAINT workflow_step_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflow(workflow_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


